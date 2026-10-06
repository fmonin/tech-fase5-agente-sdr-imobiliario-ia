"""Baixa FOTOS REAIS de banco de imagens livre (Wikimedia Commons) para os
imóveis de data/imoveis.json, substituindo as imagens ilustrativas.

Por que Wikimedia Commons (e não portais como ZAP/VivaReal/QuintoAndar)?
    - As fotos dos portais pertencem aos anunciantes/fotógrafos e os termos
      de uso proíbem copiar/raspar o conteúdo; além disso, mostrar anúncios
      reais como se fossem da nossa imobiliária seria enganoso.
    - No Commons as imagens têm licença livre (CC0, CC BY, CC BY-SA, domínio
      público). Guardamos autor e licença em data/imagens/creditos.json e a
      interface mostra o crédito embaixo de cada foto, como as licenças pedem.

As fotos são ILUSTRATIVAS do tipo de ambiente (sala, cozinha, quarto...), não
do imóvel exato — a interface deixa isso claro.

Uso (no seu computador, com internet):
    python scripts/baixar_fotos_imoveis.py              # só imagens ainda ilustrativas
    python scripts/baixar_fotos_imoveis.py --substituir # baixa tudo de novo
    python scripts/baixar_fotos_imoveis.py --imovel IM012
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ARQ_CREDITOS = RAIZ / "data" / "imagens" / "creditos.json"
API = "https://commons.wikimedia.org/w/api.php"
# A Wikimedia pede um User-Agent descritivo para scripts.
USER_AGENT = "AgenteSDRImobiliario-POC/1.0 (projeto academico FIAP; python urllib)"

BUSCAS = {
    "fachada": ["apartment building facade", "residential building exterior Brazil", "house facade modern"],
    "sala": ["living room interior apartment", "modern living room interior"],
    "cozinha": ["kitchen interior apartment", "modern kitchen interior"],
    "quarto": ["bedroom interior apartment", "modern bedroom interior"],
    "banheiro": ["bathroom interior modern", "bathroom interior apartment"],
    "varanda": ["apartment balcony", "balcony with plants apartment"],
    "area_lazer": ["condominium swimming pool", "residential swimming pool"],
    "terraco": ["rooftop terrace apartment", "penthouse terrace"],
    "ambiente_integrado": ["studio apartment interior", "small apartment interior"],
    "sala_comercial": ["office interior empty", "small office interior"],
    "recepcao": ["office reception interior", "building lobby interior"],
}
LICENCAS_OK = ("cc0", "cc by", "cc-by", "public domain", "domínio público", "pd")


def _get_json(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _baixar(url: str, destino: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        dados = resp.read()
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(dados)


def _limpar_html(texto: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", texto or "")).strip()


def buscar_candidatas(termo: str, limite: int = 30) -> list[dict]:
    dados = _get_json(
        {
            "action": "query", "format": "json", "generator": "search",
            "gsrnamespace": 6, "gsrsearch": f"{termo} filetype:bitmap", "gsrlimit": limite,
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1280,
            "iiextmetadatafilter": "LicenseShortName|Artist",
        }
    )
    candidatas = []
    for pagina in (dados.get("query", {}).get("pages", {}) or {}).values():
        info = (pagina.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        licenca = _limpar_html(meta.get("LicenseShortName", {}).get("value", ""))
        if info.get("mime") != "image/jpeg" or not licenca:
            continue
        if not any(ok in licenca.lower() for ok in LICENCAS_OK):
            continue
        if info.get("width", 0) < 1000 or info.get("width", 0) <= info.get("height", 0):
            continue  # só imagens grandes e na horizontal
        candidatas.append(
            {
                "titulo": pagina.get("title", ""),
                "url": info.get("thumburl") or info.get("url"),
                "fonte": info.get("descriptionurl", ""),
                "autor": _limpar_html(meta.get("Artist", {}).get("value", "")) or "Autor desconhecido",
                "licenca": licenca,
            }
        )
    return candidatas


def main() -> None:
    substituir = "--substituir" in sys.argv
    so_imovel = sys.argv[sys.argv.index("--imovel") + 1] if "--imovel" in sys.argv else None
    imoveis = json.loads((RAIZ / "data" / "imoveis.json").read_text(encoding="utf-8"))
    creditos = json.loads(ARQ_CREDITOS.read_text(encoding="utf-8")) if ARQ_CREDITOS.exists() else {}
    usadas = {c["fonte"] for c in creditos.values()}
    cache_busca: dict[str, list[dict]] = {}
    baixadas = falhas = 0

    for im in imoveis:
        if so_imovel and im["id"] != so_imovel:
            continue
        for foto in im.get("fotos", []):
            if foto in creditos and not substituir:
                continue  # já é foto real
            stem = Path(foto).stem
            ambiente = stem.split("_", 1)[1] if "_" in stem else stem
            escolhida = None
            for termo in BUSCAS.get(ambiente, [ambiente.replace("_", " ")]):
                if termo not in cache_busca:
                    try:
                        cache_busca[termo] = buscar_candidatas(termo)
                    except Exception as erro:  # noqa: BLE001
                        print(f"  ! busca '{termo}' falhou: {erro}")
                        cache_busca[termo] = []
                    time.sleep(0.5)  # educação com a API
                escolhida = next((c for c in cache_busca[termo] if c["fonte"] not in usadas), None)
                if escolhida:
                    break
            if not escolhida:
                print(f"  - {foto}: nenhuma foto livre encontrada (mantida a ilustrativa)")
                falhas += 1
                continue
            try:
                _baixar(escolhida["url"], RAIZ / foto)
            except Exception as erro:  # noqa: BLE001
                print(f"  ! {foto}: download falhou ({erro})")
                falhas += 1
                continue
            usadas.add(escolhida["fonte"])
            creditos[foto] = {k: escolhida[k] for k in ("titulo", "autor", "licenca", "fonte")}
            baixadas += 1
            print(f"  + {foto} <- {escolhida['titulo']} ({escolhida['licenca']})")
            time.sleep(0.3)

    ARQ_CREDITOS.parent.mkdir(parents=True, exist_ok=True)
    ARQ_CREDITOS.write_text(json.dumps(creditos, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{baixadas} foto(s) baixada(s), {falhas} mantida(s) como ilustrativa. Créditos em {ARQ_CREDITOS}")


if __name__ == "__main__":
    main()
