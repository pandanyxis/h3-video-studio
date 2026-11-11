"""Apply the personal left-to-right layout without changing generation settings."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SECTIONS = [
    ('1 · Inputs', 0, 500, '#355b46', [
        'PrimitiveStringMultiline', 'LoadImage', 'ResolutionSelector', 'PrimitiveFloat', 'ComfyMathExpression']),
    ('2 · Models', 560, 720, '#35566f', ['UNETLoader', 'CLIPLoader', 'VAELoader']),
    ('3 · LoRAs and preview', 1340, 500, '#756035', [
        'LoraLoaderModelOnly', 'Power Lora Loader (rgthree)', 'ModelPreviewOverrideKJ']),
    ('4 · Generation', 1900, 620, '#604a74', ['MiniMaxH3ImageToVideo', 'BasicGuider', 'Note']),
    ('5 · Sampling', 2580, 450, '#3e6267', [
        'RandomNoise', 'KSamplerSelect', 'BasicScheduler', 'ExtendIntermediateSigmas', 'SamplerCustomAdvanced']),
    ('6 · Video output', 3090, 500, '#70504b', ['VAEDecode', 'VAEDecodeAudio', 'VHS_VideoCombine']),
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
                    node['title'] = 'Prompt · describe your video'
                elif kind == 'LoadImage':
                    height = 340
                    node['title'] = 'Starting image · I2V'
                elif kind == 'PrimitiveFloat':
                    node['title'] = 'Video duration in seconds'
                elif kind == 'ResolutionSelector':
                    node['title'] = 'Aspect ratio and resolution'
                elif kind == 'LoraLoaderModelOnly':
                    node['title'] = 'Turbo · fixed 8-step LoRA'
                elif kind == 'Note':
                    node['title'] = 'Customize this workflow'
                    node['widgets_values'] = ['Start by editing the prompt, aspect ratio, and duration. Add style LoRAs in group 3. Use Save As to keep your own version.']
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
            node['title'] = 'Pandanyxis · custom video workflow'
            node['widgets_values'][0] = node['widgets_values'][0].split('\n\nWork from left')[0] + '\n\nWork from left to right. Group 1 holds the inputs, group 3 holds style LoRAs, and group 5 controls the seed and sampling. Preview and save the video in group 6. Use Save As to keep your own version.'
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
