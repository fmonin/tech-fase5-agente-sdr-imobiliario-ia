"""Gera imagens ILUSTRATIVAS (desenhadas) para cada foto listada em
data/imoveis.json que ainda não existe em disco.

Serve para o projeto funcionar 100% offline: cada imóvel tem pelo menos 3
imagens desde o início. Para trocar por fotos reais de banco de imagens
livres, rode depois `python scripts/baixar_fotos_imoveis.py`.

Uso:  python scripts/gerar_imagens_ilustrativas.py [--substituir]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parents[1]
LARGURA, ALTURA = 960, 640

NOMES = {
    "fachada": "Fachada", "sala": "Sala de estar", "cozinha": "Cozinha", "quarto": "Quarto",
    "banheiro": "Banheiro", "varanda": "Varanda", "area_lazer": "Área de lazer", "terraco": "Terraço",
    "ambiente_integrado": "Ambiente integrado", "sala_comercial": "Sala comercial", "recepcao": "Recepção",
}
CORES = {
    "fachada": ((120, 160, 200), (210, 225, 240)), "sala": ((200, 170, 130), (245, 235, 220)),
    "cozinha": ((150, 180, 160), (230, 240, 232)), "quarto": ((170, 150, 190), (238, 232, 245)),
    "banheiro": ((130, 180, 190), (225, 240, 242)), "varanda": ((140, 190, 140), (228, 245, 228)),
    "area_lazer": ((90, 160, 210), (215, 235, 250)), "terraco": ((200, 160, 110), (245, 232, 212)),
    "ambiente_integrado": ((190, 160, 140), (242, 234, 228)),
    "sala_comercial": ((130, 140, 160), (228, 230, 236)), "recepcao": ((150, 150, 170), (232, 232, 240)),
}


def _fonte(tamanho: int):
    for nome in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(nome, tamanho)
        except OSError:
            continue
    return ImageFont.load_default()


def _gradiente(topo, base) -> Image.Image:
    img = Image.new("RGB", (LARGURA, ALTURA))
    d = ImageDraw.Draw(img)
    for y in range(ALTURA):
        t = y / ALTURA
        d.line([(0, y), (LARGURA, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(topo, base)))
    return img


def _icone(d: ImageDraw.ImageDraw, ambiente: str, cor) -> None:
    cx, cy, w = LARGURA // 2, ALTURA // 2 - 40, 6
    if ambiente == "fachada":
        d.rectangle([cx - 150, cy - 60, cx + 150, cy + 120], outline=cor, width=w)
        d.polygon([(cx - 180, cy - 60), (cx, cy - 170), (cx + 180, cy - 60)], outline=cor, width=w)
        for x in (-100, 0, 100):
            d.rectangle([cx + x - 25, cy - 30, cx + x + 25, cy + 10], outline=cor, width=w)
        d.rectangle([cx - 25, cy + 50, cx + 25, cy + 120], outline=cor, width=w)
    elif ambiente in ("sala", "ambiente_integrado", "recepcao"):
        d.rounded_rectangle([cx - 170, cy - 20, cx + 170, cy + 80], 20, outline=cor, width=w)
        d.rounded_rectangle([cx - 140, cy - 80, cx + 140, cy - 20], 20, outline=cor, width=w)
        d.line([(cx - 150, cy + 80), (cx - 150, cy + 110)], fill=cor, width=w)
        d.line([(cx + 150, cy + 80), (cx + 150, cy + 110)], fill=cor, width=w)
    elif ambiente == "quarto":
        d.rectangle([cx - 180, cy, cx + 180, cy + 80], outline=cor, width=w)
        d.rectangle([cx - 180, cy - 100, cx - 160, cy + 110], fill=cor)
        d.rounded_rectangle([cx - 140, cy - 40, cx - 40, cy], 15, outline=cor, width=w)
    elif ambiente in ("cozinha",):
        d.rectangle([cx - 190, cy + 10, cx + 190, cy + 110], outline=cor, width=w)
        for x in (-120, -20, 80):
            d.ellipse([cx + x - 25, cy - 30, cx + x + 25, cy + 5], outline=cor, width=w)
        d.rectangle([cx - 190, cy - 140, cx + 190, cy - 70], outline=cor, width=w)
    elif ambiente == "banheiro":
        d.rounded_rectangle([cx - 160, cy, cx + 60, cy + 90], 40, outline=cor, width=w)
        d.ellipse([cx + 90, cy - 20, cx + 170, cy + 90], outline=cor, width=w)
        d.line([(cx - 120, cy - 120), (cx - 120, cy)], fill=cor, width=w)
    elif ambiente in ("varanda", "terraco"):
        d.line([(cx - 200, cy + 100), (cx + 200, cy + 100)], fill=cor, width=w)
        for x in range(-200, 201, 40):
            d.line([(cx + x, cy + 20), (cx + x, cy + 100)], fill=cor, width=w)
        d.line([(cx - 200, cy + 20), (cx + 200, cy + 20)], fill=cor, width=w)
        d.ellipse([cx + 80, cy - 160, cx + 160, cy - 80], outline=cor, width=w)
    elif ambiente == "area_lazer":
        d.rounded_rectangle([cx - 200, cy, cx + 200, cy + 100], 30, outline=cor, width=w)
        for x in (-150, -50, 50, 150):
            d.arc([cx + x - 40, cy + 30, cx + x + 40, cy + 70], 200, 340, fill=cor, width=w)
        d.ellipse([cx + 120, cy - 170, cx + 200, cy - 90], outline=cor, width=w)
    else:  # sala_comercial e outros
        d.rectangle([cx - 160, cy - 20, cx + 160, cy + 10], fill=cor)
        d.line([(cx - 140, cy + 10), (cx - 140, cy + 100)], fill=cor, width=w)
        d.line([(cx + 140, cy + 10), (cx + 140, cy + 100)], fill=cor, width=w)
        d.rectangle([cx - 60, cy - 120, cx + 60, cy - 30], outline=cor, width=w)


def gerar(imovel: dict, caminho: Path, ambiente: str) -> None:
    topo, base = CORES.get(ambiente, ((150, 150, 150), (235, 235, 235)))
    img = _gradiente(topo, base)
    d = ImageDraw.Draw(img)
    _icone(d, ambiente, (255, 255, 255))
    d.text((40, ALTURA - 150), NOMES.get(ambiente, ambiente.title()), font=_fonte(44), fill=(40, 40, 50))
    d.text((40, ALTURA - 95), f"{imovel['titulo']} · {imovel['bairro']}", font=_fonte(24), fill=(60, 60, 70))
    d.rounded_rectangle([LARGURA - 270, 24, LARGURA - 24, 66], 12, fill=(255, 255, 255))
    d.text((LARGURA - 256, 34), "Imagem ilustrativa", font=_fonte(20), fill=(90, 90, 100))
    caminho.parent.mkdir(parents=True, exist_ok=True)
    img.save(caminho, "JPEG", quality=82)


def main() -> None:
    substituir = "--substituir" in sys.argv
    imoveis = json.loads((RAIZ / "data" / "imoveis.json").read_text(encoding="utf-8"))
    total = 0
    for im in imoveis:
        for foto in im.get("fotos", []):
            caminho = RAIZ / foto
            if caminho.exists() and not substituir:
                continue
            ambiente = caminho.stem.split("_", 1)[1] if "_" in caminho.stem else caminho.stem
            gerar(im, caminho, ambiente)
            total += 1
    print(f"{total} imagem(ns) ilustrativa(s) gerada(s).")


if __name__ == "__main__":
    main()
