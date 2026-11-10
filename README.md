# H3 Video Studio

Voer een verhaal en één tot negen referentiebeelden in. Studio maakt vijf samenhangende shots met MiniMax H3 en exporteert ze als één MP4. De browserinterface staat in `studio.html` en de projectservice in `studio_web.py`.

Je voert een shotidee of verhaal in. De eigen Studio-planner werkt dit met Qwen uit tot vijf shots. Daarna schrijft de eigen Studio Writer in een aparte stap de volledige H3-prompt per shot, inclusief beweging, camera, geluid en begin- en eindstand. Beide stappen staan in `studio_writer.py` en gebruiken de lokale Qwen-server. ComfyUI met de H3 Continuity-renderlaag rendert de clips op de GPU. Runpod levert de GPU, opslag en SSH-toegang. Zie [het modelprofiel](runpod/RUNPOD-MODELS.md) voor de modellen.

## GPU-host starten

GitHub Actions bouwt de image vanaf de repo-root met `runpod/Dockerfile` en publiceert deze naar `ghcr.io/pandanyxis/h3-video-studio`. Gebruik een commit-tag om een specifieke build te starten.

```sh
python runpod/make_template.py --image ghcr.io/pandanyxis/h3-video-studio:COMMIT_SHA
```

Gebruik de gegenereerde configuratie voor een Pod met een RTX PRO 6000 Blackwell 96 GB en CUDA 13. Voor een private image heeft Runpod leesrechten nodig via Container Registry Auth. Koppel persistente opslag aan `/workspace` om projecten te bewaren.

De Studio-interface luistert op de loopback-interface van de Pod. Open een SSH-tunnel en ga in de browser naar [Studio op localhost](http://localhost:8190/).

```sh
ssh -N -L 8190:127.0.0.1:8190 -p SSH_PORT root@POD_IP
```

Voeg een verhaal en referentiebeelden toe en klik op **Maak mijn film**. De projectpagina toont de status en biedt de MP4 aan zodra de export klaar is. Je vindt eerdere projecten in de zijbalk. Studio bewaart het shotplan en de prompts en pakt actieve projecten na een herstart weer op. Bij een onderbreking kun je het productielog openen en de verwerking hervatten. Bewaarde clips en renderaanvragen worden hergebruikt. Een mislukte of onzekere GPU-taak kan herstel in de renderdienst vereisen.

## Uitvoeren via SSH

```sh
python /opt/studio/run_studio.py verhaal.txt /workspace/studio/projects/proef referentie-a.png
```

De projectmap bevat het shotplan, de tijdlijn, de render-ID, vijf clips en `film.mp4`. Browserprojecten bewaren ook de invoer en `render.log`. Herhaal het CLI-commando met dezelfde projectmap om een bestaande render op te vragen en de export af te maken. Gebruik voor een nieuw verhaal een nieuwe projectmap.

## Interface lokaal bekijken

```sh
python -m pip install -r requirements.txt
python studio_web.py --projects ./projects
```

Open http://localhost:8190. De productie-worker vereist de Linux GPU-host met Qwen, ComfyUI en ffmpeg. Lokale projecten blijven gescheiden van de projecten op de GPU-host.

## Controles

```sh
python -m pip install -r requirements.txt requests tqdm
PYTHONPATH=.:runpod python -m unittest discover -s tests
PYTHONPATH=.:runpod python -m unittest discover -s runpod/tests
```
