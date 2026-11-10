from __future__ import annotations

import threading
import time

from .contract import ModelError


class RouterLifecycle:
    """Track only explicitly selected models using the llama.cpp router protocol."""

    def __init__(self, request):
        self.request = request
        self.models = {}
        self.lock = threading.RLock()
        self._snapshot = {}
        self._refreshing = False
        self._revision = 0
        self._owned = set()
        self._transitions = {}

    def entries(self, endpoint):
        data = self.request(endpoint, "GET", "/models", timeout=3).get("data")
        if not isinstance(data, list) or not data or not all(
            isinstance(item, dict) and isinstance(item.get("id"), str)
            and isinstance(item.get("status"), dict)
            and item["status"].get("value") in {"loaded", "unloaded", "loading", "sleeping", "downloading", "downloaded"}
            for item in data
        ):
            raise ModelError("EXTERNAL_ROUTER_UNAVAILABLE", "The server does not report router lifecycle states.")
        return data

    def register(self, model):
        if model.get("lifecycle_supported"):
            with self.lock:
                self.models[model["id"]] = dict(model)

    def target(self, model_id):
        with self.lock:
            model = self.models.get(model_id)
        if not model:
            raise ModelError("EXTERNAL_LIFECYCLE_UNAVAILABLE", "Select a concrete llama.cpp router model before managing its lifecycle.")
        return model

    def state(self, model):
        matches = [item for item in self.entries(model["endpoint"]) if item["id"] == model["remote_model"]]
        if len(matches) != 1:
            raise ModelError("EXTERNAL_MODEL_NOT_FOUND", "The selected router model is missing or ambiguous.")
        return matches[0]["status"]

    def transition(self, model_id, loaded, cancelled=lambda: False, timeout=120):
        model = self.target(model_id)
        with self.lock:
            self._transitions[model_id] = "loading" if loaded else "unloading"
        try:
            return self._transition(model, loaded, cancelled, timeout)
        finally:
            with self.lock:
                self._transitions.pop(model_id, None)

    def _transition(self, model, loaded, cancelled, timeout):
        desired = "loaded" if loaded else "unloaded"
        with self.lock:
            self._revision += 1
            self._snapshot.pop(model["endpoint"], None)
        current = self.state(model)
        if current["value"] == desired:
            self._confirmed(model, desired, loaded)
            return
        if loaded and cancelled():
            raise ModelError("GENERATION_CANCELLED", "Generation was cancelled before model loading.")
        if loaded and current["value"] == "sleeping":
            # The router rejects load while the sleeping child process still exists.
            self._transition(model, False, cancelled, timeout)
            current = self.state(model)
        if loaded and current["value"] in {"downloading", "downloaded"}:
            raise ModelError("EXTERNAL_MODEL_NOT_READY", "Finish preparing the selected model in llama.cpp before generating.")
        if not (loaded and current["value"] == "loading"):
            response = self.request(model["endpoint"], "POST", "/models/load" if loaded else "/models/unload",
                                    {"model": model["remote_model"]}, timeout=timeout)
            if response.get("success") is not True:
                raise ModelError("EXTERNAL_LIFECYCLE_FAILED", "The router did not accept the model lifecycle request.")
        deadline = time.monotonic() + timeout
        while True:
            current = self.state(model)
            if current["value"] == desired:
                self._confirmed(model, desired, loaded)
                return
            if loaded and (cancelled() or current.get("failed")):
                raise ModelError("GENERATION_CANCELLED" if cancelled() else "EXTERNAL_LOAD_FAILED",
                                 "The router did not finish loading the selected model.")
            if time.monotonic() >= deadline:
                raise ModelError("EXTERNAL_LIFECYCLE_TIMEOUT", f"The router did not confirm model state: {desired}.")
            time.sleep(.25)

    def _confirmed(self, model, state, owned):
        with self.lock:
            self._revision += 1
            if owned:
                self._owned.add(model["id"])
            else:
                self._owned.discard(model["id"])
            previous = self._snapshot.get(model["endpoint"], {})
            states = dict(previous.get("states", {}))
            states[model["id"]] = state
            self._snapshot[model["endpoint"]] = {"states": states, "time": time.monotonic(), "ttl": 2}

    def residency(self):
        with self.lock:
            models = list(self.models.values())
        targets = []
        endpoints = {}
        for model in models:
            endpoint = model["endpoint"]
            if endpoint not in endpoints:
                try:
                    endpoints[endpoint] = self.entries(endpoint)
                except ModelError:
                    endpoints[endpoint] = []
            matches = [item for item in endpoints[endpoint] if item["id"] == model["remote_model"]]
            state = matches[0]["status"]["value"] if len(matches) == 1 else "unknown"
            with self.lock:
                if state == "unloaded":
                    self._owned.discard(model["id"])
                targets.append(self._target_snapshot(model, state))
        return {"targets": targets}

    def _target_snapshot(self, model, state):
        return {"model_id": model["id"], "endpoint": model["endpoint"],
                "state": self._transitions.get(model["id"], state),
                "writer_owned": model["id"] in self._owned}

    def snapshot(self):
        """Serve status immediately; refresh router endpoints outside the request."""
        with self.lock:
            models = list(self.models.values())
            now = time.monotonic()
            targets = []
            stale = set()
            for model in models:
                cached = self._snapshot.get(model["endpoint"])
                fresh = cached and now - cached["time"] < cached["ttl"]
                entries = cached["states"] if fresh else {}
                state = entries.get(model["id"], "unknown")
                targets.append(self._target_snapshot(model, state))
                if not fresh:
                    stale.add(model["endpoint"])
            if stale and not self._refreshing:
                self._refreshing = True
                threading.Thread(target=self._refresh_snapshot, args=(stale, self._revision), daemon=True).start()
            return {"targets": targets}

    def _refresh_snapshot(self, endpoints, revision):
        try:
            with self.lock:
                models = list(self.models.values())
            for endpoint in endpoints:
                try:
                    entries = self.entries(endpoint)
                    states = {}
                    for model in models:
                        if model["endpoint"] != endpoint:
                            continue
                        matches = [e for e in entries if e["id"] == model["remote_model"]]
                        states[model["id"]] = matches[0]["status"]["value"] if len(matches) == 1 else "unknown"
                    ttl = 2
                except Exception:
                    states, ttl = {}, 15
                with self.lock:
                    if revision == self._revision:
                        for model_id, state in states.items():
                            if state == "unloaded":
                                self._owned.discard(model_id)
                        self._snapshot[endpoint] = {"states": states, "time": time.monotonic(), "ttl": ttl}
        finally:
            with self.lock:
                self._refreshing = False
