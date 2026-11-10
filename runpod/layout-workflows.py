"""Apply the personal left-to-right layout without changing generation settings."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SECTIONS = [
    ('1 · Invoer', 0, 500, '#355b46', [
        'PrimitiveStringMultiline', 'LoadImage', 'ResolutionSelector', 'PrimitiveFloat', 'ComfyMathExpression']),
    ('2 · Modellen', 560, 720, '#35566f', ['UNETLoader', 'CLIPLoader', 'VAELoader']),
    ('3 · LoRA’s en preview', 1340, 500, '#756035', [
        'LoraLoaderModelOnly', 'Power Lora Loader (rgthree)', 'ModelPreviewOverrideKJ']),
    ('4 · Generatie', 1900, 620, '#604a74', ['MiniMaxH3ImageToVideo', 'BasicGuider', 'Note']),
    ('5 · Sampling', 2580, 450, '#3e6267', [
        'RandomNoise', 'KSamplerSelect', 'BasicScheduler', 'ExtendIntermediateSigmas', 'SamplerCustomAdvanced']),
    ('6 · Video-uitvoer', 3090, 500, '#70504b', ['VAEDecode', 'VAEDecodeAudio', 'VHS_VideoCombine']),
]


def layout(document):
    groups = []
    placed = set()
    for index, (title, x, width, color, types) in enumerate(SECTIONS, 1):
        y = 100
        for kind in types:
            for node in document['nodes']:
                if node['type'] != kind:
                    continue
                height = max(80, node['size'][1])
                if kind == 'PrimitiveStringMultiline':
                    height = 240
                    node['title'] = 'Prompt · beschrijf jouw video'
                elif kind == 'LoadImage':
                    height = 340
                    node['title'] = 'Startafbeelding · I2V'
                elif kind == 'PrimitiveFloat':
                    node['title'] = 'Videoduur in seconden'
                elif kind == 'ResolutionSelector':
                    node['title'] = 'Beeldformaat en resolutie'
                elif kind == 'LoraLoaderModelOnly':
                    node['title'] = 'Turbo · vaste 8-step LoRA'
                elif kind == 'Note':
                    node['title'] = 'Zelf verder bouwen'
                    node['widgets_values'] = ['Wijzig eerst prompt, beeldformaat en duur. Voeg eigen stijl-LoRA’s toe in groep 3. Bewaar jouw eigen variant via Save As.']
                    height = 140
                node['pos'] = [x + 25, y]
                node['size'] = [width - 50, height]
                placed.add(node['id'])
                y += height + 55
        groups.append({'id': index, 'title': title, 'bounding': [x, 30, width, y - 30],
                       'color': color, 'font_size': 24, 'flags': {}})
    for node in document['nodes']:
        if node['type'] == 'MarkdownNote':
            node['pos'] = [0, -270]
            node['size'] = [1120, 230]
            node['title'] = 'Pandanyxis · eigen video-workflow'
            node['widgets_values'][0] = node['widgets_values'][0].split('\n\nWerk van links')[0] + '\n\nWerk van links naar rechts. Groep 1: jouw invoer. Groep 3: eigen stijl-LoRA’s. Groep 5: seed en sampling. Groep 6: bekijk en bewaar je video. Maak via Save As een eigen bouwversie.'
            placed.add(node['id'])
    missing = {n['id'] for n in document['nodes']} - placed
    if missing:
        raise ValueError('Unplaced workflow nodes: ' + str(missing))
    document['groups'] = groups
    document.setdefault('extra', {})['ds'] = {'scale': 0.38, 'offset': [100, 330]}


if __name__ == '__main__':
    for path in sorted(ROOT.glob('workflow-*.json')):
        original = json.loads(path.read_text(encoding='utf-8'))
        updated = json.loads(json.dumps(original))
        layout(updated)
        assert updated['links'] == original['links']
        for before, after in zip(original['nodes'], updated['nodes']):
            assert before['inputs'] == after['inputs']
            assert before.get('outputs') == after.get('outputs')
            if before['type'] not in ('Note', 'MarkdownNote'):
                assert before.get('widgets_values') == after.get('widgets_values')
                assert before.get('widgets_values_named') == after.get('widgets_values_named')
        path.write_text(json.dumps(updated, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
        print('Updated layout; links and generation settings preserved: ' + path.name)
