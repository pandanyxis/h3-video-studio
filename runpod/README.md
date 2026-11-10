# GPU-runtime voor H3 Video Studio

Runpod levert één GPU voor Qwen en MiniMax H3. De eigen Studio-interface draait mee in de image. De projectservice stuurt het plannen, schrijven, renderen en exporteren aan.

## Image en opslag

Bouw vanaf de repo-root met `runpod/Dockerfile` of gebruik de gepubliceerde image met de tag van een geslaagde GitHub-build.

```sh
python runpod/make_template.py --image ghcr.io/pandanyxis/h3-video-studio:COMMIT_SHA
```

Het script schrijft `runpod-template.json`, een payload voor de Runpod REST-template. Kies een RTX PRO 6000 Blackwell met 96 GB VRAM en een driver die CUDA 13 ondersteunt. De template gebruikt 50 GB containerschijf en 120 GB op `/workspace`. De standaardgewichten nemen samen ongeveer 70 GiB in. De rest biedt plaats aan de downloadcache en je projecten. Reserveer extra ruimte als je meer modellen toevoegt.

De modelscripts verifiëren de bestanden op de Pod. Een volgende start op hetzelfde volume kan de bestanden hergebruiken. Wil je projecten meenemen naar een nieuwe Pod, gebruik dan een gekoppeld netwerkvolume of exporteer de projectmap.

## Toegang

De template publiceert alleen de Studio-interface op poort `8190/http`. Maak een Runpod Secret `STUDIO_PASSWORD` met minstens 16 tekens. De template verwijst naar dat Secret. Open in Runpod **Connect → HTTP Service (8190)** en log in met gebruikersnaam `studio` en dat wachtwoord. Elke nieuwe Pod krijgt een eigen HTTPS-adres. Zonder wachtwoordconfiguratie geeft Studio geen toegang tot projecten.

Qwen en ComfyUI luisteren binnen de Pod. Bij de eerste start downloaden en verifiëren ze de gewichten. De logregels `[qwen]` en `[comfy]` laten de voortgang zien.

De runtime genereert standaard een Qwen-sleutel en bewaart die privé op `/workspace`. Voor een vaste sleutel kun je `LLAMA_API_KEY` via Runpod Secrets instellen. Gebruik eventueel `HF_TOKEN` of `CIVITAI_TOKEN` voor aanvullende downloads. Houd `runtime-settings.json` met lokale instellingen buiten Git. Zie [de modelconfiguratie](RUNPOD-MODELS.md) voor opties.

## GPU-proef

Open Studio, geef een kort verhaal met één referentiebeeld en start een film. Controleer of het project per gepland shot één prompt en één losse clip bevat en of de MP4 afspeelt. Controleer ook of de clips binnen tien seconden blijven. Stop een testpod na afloop om GPU-kosten te beperken.
