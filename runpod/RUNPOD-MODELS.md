# Runpod-modelprofiel

De standaardbuild gebruikt MiniMax H3 FL2VA Pruned INT8 van Comfy-Org, plus de bestaande tekstencoder, audio- en video-VAE, Turbo-LoRA en preview. Qwen genereert de shotprompts voor Studio. Eros, Mystic en de privémirror worden niet automatisch opgehaald. Dat scheelt circa 22 GB aan downloads.

De standaard T2V- en I2V-workflows wijzen naar het Comfy-Org-model. Bestaande persoonlijke workflows worden niet overschreven. Pas op een bestaand volume zo nodig de UNET-loader aan.

## Extra modellen bij opstart

Kies in Studio per project een renderprofiel en een beeldverhouding. Automatisch gebruikt de oriëntatie van je eerste referentiebeeld. Je kunt ook 16:9 (1024 × 576) of 9:16 (576 × 1024) kiezen.

Kies in Studio onder **Renderprofiel** voor **Eigen renderinstellingen**. Kies een geïnstalleerd MiniMax H3-model, een bijbehorende LoRA, het aantal samplingstappen en de LoRA-sterkte. Download extra bestanden via de velden hieronder.

Voor vooraf ingestelde profielen kun je in Runpod optioneel `STUDIO_RENDER_PROFILES` invullen. Gebruik de exacte bestandsnamen zoals ComfyUI ze ziet.

```json
[{"id":"eigen-h3","title":"Eigen H3","model":"mijn-h3.safetensors","lora":"mijn-lora.safetensors","steps":8,"lora_strength":0.8}]
```

Stel HF_TOKEN en CIVITAI_TOKEN in via Runpod Secrets. Selecteer de downloads met:

- CIVITAI_CHECKPOINTS, CIVITAI_LORAS en CIVITAI_DIFFUSION_MODELS. Gebruik kommagescheiden identifiers zoals model:123 of version:456.
- HF_EXTRA_MODELS. Geef een JSON-lijst op, bijvoorbeeld [{"repo":"eigenaar/repo","revision":"COMMIT_SHA","file":"pad/extra.safetensors","folder":"loras"}].

Gebruik unieke bestandsnamen. Een bestaand bestand op de bestemming wordt hergebruikt. Reserveer ruimte voor de HF-downloadcache.

Civitai-bestanden vanaf 1 GiB gebruiken standaard vier verbindingen als de downloadserver bytebereiken ondersteunt. Met `CIVITAI_DOWNLOAD_WORKERS` kies je 1 tot 8 verbindingen. Kies 1 voor een enkele verbinding. De downloader controleert altijd de SHA-256 van het volledige bestand.

## Validatie uitsluitend op Runpod

Bouw op Runpod met de repo-root als buildcontext en runpod/Dockerfile als Dockerfile. Geef de eigen image expliciet mee aan make_template.py via --image. Start op een RTX PRO 6000 Blackwell 96 GB met lege modelopslag. Controleer de standaarddownloads, render een korte T2V- en I2V-clip en meet piek-VRAM. Test daarna een Civitai-extra en een HF-extra, inclusief hergebruik bij een tweede start.

