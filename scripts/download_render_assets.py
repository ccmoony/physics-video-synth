from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from urllib.request import Request
from urllib.request import urlopen


WORKSPACE_DIR = Path(__file__).resolve().parents[1]
ASSET_DIR = WORKSPACE_DIR / "assets" / "polyhaven"
AMBIENTCG_DIR = WORKSPACE_DIR / "assets" / "ambientcg"
USER_AGENT = "synthetic-blender-asset-downloader/1.0"

ASSETS = {
    "brown_photostudio_05": {
        "type": "hdri",
        "source_url": "https://polyhaven.com/a/brown_photostudio_05",
        "files": {
            "brown_photostudio_05_2k.hdr": "https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/2k/brown_photostudio_05_2k.hdr",
        },
    },
    "wood_floor_worn": {
        "type": "texture",
        "source_url": "https://polyhaven.com/a/wood_floor_worn",
        "recommended_for": "worn floor variation with natural scratches, knots, seams, and color variation",
        "files": {
            "wood_floor_worn_ao_4k.jpg": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/wood_floor_worn/wood_floor_worn_ao_4k.jpg",
            "wood_floor_worn_diff_4k.jpg": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/wood_floor_worn/wood_floor_worn_diff_4k.jpg",
            "wood_floor_worn_disp_4k.png": "https://dl.polyhaven.org/file/ph-assets/Textures/png/4k/wood_floor_worn/wood_floor_worn_disp_4k.png",
            "wood_floor_worn_nor_gl_4k.jpg": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/wood_floor_worn/wood_floor_worn_nor_gl_4k.jpg",
            "wood_floor_worn_rough_4k.jpg": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/wood_floor_worn/wood_floor_worn_rough_4k.jpg",
        },
    },
    "wood_table": {
        "type": "texture",
        "source_url": "https://polyhaven.com/a/wood_table",
        "recommended_for": "wooden block faces; 4k maps make the block less uniform in close-ups",
        "files": {
            "wood_table_ao_4k.jpg": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/wood_table/wood_table_ao_4k.jpg",
            "wood_table_diff_4k.jpg": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/wood_table/wood_table_diff_4k.jpg",
            "wood_table_disp_4k.png": "https://dl.polyhaven.org/file/ph-assets/Textures/png/4k/wood_table/wood_table_disp_4k.png",
            "wood_table_nor_gl_4k.jpg": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/wood_table/wood_table_nor_gl_4k.jpg",
            "wood_table_rough_4k.jpg": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/wood_table/wood_table_rough_4k.jpg",
        },
    },
    "stained_pine": {
        "type": "texture",
        "source_url": "https://polyhaven.com/a/stained_pine",
        "recommended_for": "warmer varnished pine option for wood blocks with less striped close-up appearance",
        "files": {
            "stained_pine_ao_4k.jpg": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/stained_pine/stained_pine_ao_4k.jpg",
            "stained_pine_diff_4k.jpg": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/stained_pine/stained_pine_diff_4k.jpg",
            "stained_pine_disp_4k.png": "https://dl.polyhaven.org/file/ph-assets/Textures/png/4k/stained_pine/stained_pine_disp_4k.png",
            "stained_pine_nor_gl_4k.jpg": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/stained_pine/stained_pine_nor_gl_4k.jpg",
            "stained_pine_rough_4k.jpg": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/stained_pine/stained_pine_rough_4k.jpg",
        },
    },
}

AMBIENTCG_ZIP_ASSETS = {
    "Rubber002": {
        "type": "texture",
        "source": "ambientCG",
        "license": "CC0",
        "source_url": "https://ambientcg.com/view?id=Rubber002",
        "zip_url": "https://ambientcg.com/get?file=Rubber002_4K-JPG.zip",
        "recommended_for": "seamless rubber micro-surface for ball roughness, normal, and height detail",
        "files": {
            "Rubber002_4K-JPG_Color.jpg": "Rubber002_4K-JPG_Color.jpg",
            "Rubber002_4K-JPG_Displacement.jpg": "Rubber002_4K-JPG_Displacement.jpg",
            "Rubber002_4K-JPG_NormalGL.jpg": "Rubber002_4K-JPG_NormalGL.jpg",
            "Rubber002_4K-JPG_Roughness.jpg": "Rubber002_4K-JPG_Roughness.jpg",
        },
    },
}


def download(url: str, path: Path) -> dict[str, object]:
    if path.exists() and path.stat().st_size > 0:
        return {"path": str(path.relative_to(WORKSPACE_DIR)), "size": path.stat().st_size, "cached": True}
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.md5()
    total = 0
    request = Request(url, headers={"User-Agent": USER_AGENT})
    partial_path = path.with_suffix(path.suffix + ".part")
    with urlopen(request, timeout=120) as response, partial_path.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            digest.update(chunk)
            total += len(chunk)
    partial_path.replace(path)
    return {
        "path": str(path.relative_to(WORKSPACE_DIR)),
        "size": total,
        "md5": digest.hexdigest(),
        "cached": False,
    }


def file_metadata(path: Path, *, cached: bool) -> dict[str, object]:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return {
        "path": str(path.relative_to(WORKSPACE_DIR)),
        "size": path.stat().st_size,
        "md5": digest.hexdigest(),
        "cached": cached,
    }


def download_ambientcg_zip_asset(asset_name: str, asset: dict[str, object]) -> dict[str, object]:
    asset_dir = AMBIENTCG_DIR / asset_name
    asset_dir.mkdir(parents=True, exist_ok=True)
    files = asset["files"]
    assert isinstance(files, dict)
    cached = all((asset_dir / filename).exists() and (asset_dir / filename).stat().st_size > 0 for filename in files)
    if not cached:
        zip_path = asset_dir / f"{asset_name}_download.zip.part"
        print(f"Downloading {asset_name}/{zip_path.name}")
        download(str(asset["zip_url"]), zip_path)
        with zipfile.ZipFile(zip_path) as archive:
            for filename, member_name in files.items():
                target = asset_dir / filename
                print(f"Extracting {asset_name}/{filename}")
                with archive.open(str(member_name)) as source, target.open("wb") as handle:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
        zip_path.unlink(missing_ok=True)

    return {
        "type": asset["type"],
        "source": asset["source"],
        "license": asset["license"],
        "source_url": asset["source_url"],
        "recommended_for": asset.get("recommended_for"),
        "files": {
            filename: file_metadata(asset_dir / filename, cached=cached)
            for filename in files
        },
    }


def main() -> None:
    manifest: dict[str, object] = {
        "source": "Poly Haven",
        "license": "CC0",
        "assets": {},
    }
    for asset_name, asset in ASSETS.items():
        asset_dir = ASSET_DIR / asset_name
        files = {}
        for filename, url in asset["files"].items():
            print(f"Downloading {asset_name}/{filename}")
            files[filename] = download(url, asset_dir / filename)
        manifest["assets"][asset_name] = {
            "type": asset["type"],
            "source_url": asset["source_url"],
            "recommended_for": asset.get("recommended_for"),
            "files": files,
        }
    manifest_path = ASSET_DIR / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {manifest_path}")

    ambient_manifest: dict[str, object] = {
        "source": "ambientCG",
        "license": "CC0",
        "assets": {},
    }
    for asset_name, asset in AMBIENTCG_ZIP_ASSETS.items():
        ambient_manifest["assets"][asset_name] = download_ambientcg_zip_asset(asset_name, asset)
    ambient_manifest_path = AMBIENTCG_DIR / "manifest.json"
    ambient_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    ambient_manifest_path.write_text(json.dumps(ambient_manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {ambient_manifest_path}")


if __name__ == "__main__":
    main()
