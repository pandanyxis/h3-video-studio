# Zelf verder bouwen in ComfyUI

Open een workflow uit **Personal** in ComfyUI. Gebruik de variant met `text-to-video` voor T2V en `image-to-video` voor I2V. De bestandsnaam vermeldt ook welk model wordt geladen: `minimax` of `eros`.

De indeling loopt van links naar rechts:

| Groep | Wat je hier doet |
|---|---|
| 1 · Invoer | Prompt schrijven, startafbeelding kiezen bij I2V, beeldformaat en videoduur instellen. |
| 2 · Modellen | Diffusiemodel, Qwen3-VL-tekstencoder en audio/video-VAE laden. |
| 3 · LoRA’s en preview | Eigen stijl-LoRA’s toevoegen en de preview instellen. De vaste Turbo-LoRA hoort bij de 8-step-opzet. |
| 4 · Generatie | Invoer en modellen samenbrengen voor de videogeneratie. |
| 5 · Sampling | Seed, sampler en de bijbehorende stappen instellen. |
| 6 · Video-uitvoer | Video en audio decoderen, combineren en het resultaat bewaren. |

Sla eerst een kopie onder jouw eigen naam op. Zo kun je experimenteren en de meegeleverde versie terugvinden. Een andere checkpoint of LoRA kan eigen instellingen of een andere workflow nodig hebben.

## Extra Civitai-modellen

Voeg bij de pod of template de gewenste identifiers toe. Meerdere waarden scheid je met komma’s.

| Omgevingsvariabele | Bestemming |
|---|---|
| `CIVITAI_DIFFUSION_MODELS` | Modellen voor de diffusion/UNET-loader |
| `CIVITAI_LORAS` | LoRA’s |
| `CIVITAI_CHECKPOINTS` | Complete checkpoints voor een checkpoint-loader |

Gebruik `version:123456` voor een specifieke modelversie. Voor een specifieke quantisatie gebruik je de Civitai-model-URL met `modelVersionId` én `fileId`. Zet je token uitsluitend in `CIVITAI_TOKEN` of `civitai_token`.

De downloader controleert de SHA256-hash uit Civitai. Selecteer na het downloaden het bestand in de passende loader. Als het nog niet zichtbaar is, vernieuw de modellenlijst of herlaad ComfyUI.

## Je eigen werk bewaren

Deze opzet gebruikt alleen tijdelijke podopslag. Download je aangepaste workflow-JSON en je video's voordat je de pod stopt of verwijdert. Ook chats en geüploade afbeeldingen moet je zelf bewaren als je ze wilt houden.

Wil je jouw aangepaste workflow bij elke nieuwe pod terugzien? Exporteer de JSON, vervang het bijbehorende `workflow-*.json`-bestand in deze repository en push de wijziging naar `main`. GitHub bouwt dan een nieuwe image. Gebruik die nieuwe image voor je volgende pod. Reeds aangemaakte pods krijgen deze wijziging niet automatisch.

`layout-workflows.py` past de meegeleverde groepsindeling opnieuw toe op de zes workflows. Maak een reservekopie voordat je het script op een zelf aangepaste workflow gebruikt; nieuwe nodetypes vereisen eerst een uitbreiding van de indeling in het script.

## Meer geheugen voor video

Met `ENABLE_QWEN=false` start de aparte Qwen-chatserver niet mee en blijft meer GPU-geheugen beschikbaar voor video. De Qwen3-VL-tekstencoder binnen de video-workflow blijft nodig. Een hogere resolutie of langere video vraagt meer geheugen en rekentijd.

## Automatisch geheugen vrijgeven

De image bevat `combined-auto-unload`. Na de laatste generatie in de wachtrij,
ook bij een uitvoerfout, vraagt deze ComfyUI om modellen te unloaden en de
uitvoeringscache te wissen. ComfyUI ruimt daarna ongebruikte objecten en GPU-cache
op vanuit de uitvoeringsthread. Binnen een batch blijven modellen geladen;
de volgende batch laadt ze opnieuw. Modelbestanden en opgeslagen resultaten
blijven op schijf. Dit geldt voor alle workflows zonder extra canvasnodes.
Bouw de image opnieuw en start ComfyUI met die image om dit op een bestaande pod
te activeren.
