# H3 Video Studio

Doel: verhaal + referentiebeelden → 5, 10 of 20 shots → MiniMax H3 → één MP4 op één Runpod.

## Eerste bouwstap

- `writer/` bevat H3 Learning Writer met zijn bestaande Learning Center en licentie.
- `runpod/` bevat de bestaande MiniMax H3 / Qwen Docker-runtime. Continuity Studio is vastgezet in `comfy-sources.json`. Het bestaande buildscript installeert de node-pack en zijn dependencies.
- `plan_story.py` zet een tekstverhaal om in een gevalideerd JSON-shotplan via Qwen.

Bronrevisies: Writer `f5ab3e0e0c310be42faf23a092231a75eeecd5ef`, Runpod `42554d60ecfd28ba190ae74b18addf03c87342a9`, Continuity Studio `e6a37dcf36d9be541a5cbde0667720836e51b615`.

## Planner proberen

Draai dit op de machine waar Qwen bereikbaar is. Stel indien nodig `LLAMA_API_KEY` in als omgevingsvariabele.

```sh
python plan_story.py verhaal.txt shots.json --shots 5
```

Gebruik `--endpoint` voor een afwijkende OpenAI-compatibele basis-URL inclusief `/v1`.

## Open werk voor de eerste Runpod-release

1. Writer en ComfyUI via de SSH-tunnel op Runpod verifiëren.
2. Referenties toevoegen aan het shotplan, Writer-prompts genereren en het plan koppelen aan een Continuity-project met de bestaande H3-modellen.
3. GPU-taken serialiseren en het geheugen tussen Qwen en H3 vrijgeven. Bewaar de voortgang en clips per shot op persistente opslag.
4. Clips met beeld en geluid exporteren naar één MP4. Voeg een koppeling naar het Learning Center toe.
5. Bouw de image met de repo-root als buildcontext en `runpod/Dockerfile` als Dockerfile. Test node-import, render vijf korte shots op één RTX PRO 6000 Blackwell 96 GB en meet piek-VRAM. Test hervatten na een herstart en speel de geëxporteerde MP4 af.

De gecombineerde container en GPU-workflow moeten nog worden gevalideerd.

Zie [Runpod-modelprofiel](runpod/RUNPOD-MODELS.md) voor de aangepaste standaardmodellen, optionele downloads en de validatie op Runpod.

## MP4-export

Op Runpod met ffmpeg: python export_project.py /workspace/project /workspace/project/film.mp4

Het project bevat shots.json en clips/shot-01.mp4, clips/shot-02.mp4 enzovoort. De clips moeten dezelfde codec, beeldgrootte, framerate en audio-indeling hebben. De export vereist alle shots en bewaart de losse clips.

## Continuity-tijdlijn voorbereiden

Op Runpod: python prepare_timeline.py shots.json timeline.json --name Proef --profile PROFIEL

Gebruik een profiel dat de Continuity-capabilities op de Pod als gereed melden. Dit zet de velden per shot om in een Continuity-projectdocument. Modelmapping, referentieuploads, Writer-promptgeneratie en automatische indiening blijven open.

## Eigen Studio-image en Runpod-template

Publiceer deze code in de nieuwe GitHub-repository pandanyxis/h3-video-studio. De GitHub Actions-workflow bouwt vanaf de repo-root met runpod/Dockerfile en publiceert naar ghcr.io/pandanyxis/h3-video-studio. Gebruik bij voorkeur de tag van een specifieke commit. Voor een private package moet Runpod leesrechten krijgen via Container Registry Auth.

Maak de template na publicatie met python runpod/make_template.py --image ghcr.io/pandanyxis/h3-video-studio:COMMIT_SHA. Selecteer bij het aanmaken van de Pod een RTX PRO 6000 of RTX PRO 6000 WK met 96 GB, zonder regiofilter. Gebruik Runpod Secrets voor credentials. Koppel persistente opslag op /workspace als projecten en Writer-data bewaard moeten blijven na vervanging van een Pod.

De template publiceert alleen SSH. Open Writer en ComfyUI via dezelfde SSH-verbinding:

    ssh -N -L 8190:127.0.0.1:8190 -L 8188:127.0.0.1:8188 -L 8080:127.0.0.1:8080 -p SSH_PORT root@POD_IP

Open Writer op http://localhost:8190 en ComfyUI met Continuity Studio op http://localhost:8188. Configureer in Writer de Qwen-provider met http://127.0.0.1:8080/v1 en de Qwen-sleutel. De planner, tijdlijnvoorbereiding en export staan in de container onder /opt/studio. Automatische aansturing vanuit de browser en GPU-beurtregeling zijn nog open.


## Automatische productie op Runpod

Voer op de Studio-pod via SSH uit:

```sh
python /opt/studio/run_studio.py verhaal.txt /workspace/projecten/proef referentie-a.png referentie-b.jpg
```

Qwen leest de beelden en maakt vijf prompts met de referentiegids van Writer.
Continuity rendert met het profiel `studio-turbo8`. De projectmap bevat het
shotplan, de tijdlijn, de render-ID, vijf clips en `film.mp4`. Herhaal het
commando met dezelfde projectmap om een eerdere render op te vragen en de
export af te maken. Kies voor een nieuw verhaal een nieuwe projectmap.
