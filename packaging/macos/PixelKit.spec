# Run through scripts/build_macos.py on the target Mac architecture.
import os
import re
from pathlib import Path

project = Path(SPECPATH).resolve().parents[1]
prefix = Path(os.environ["PIXELKIT_MAGICK_PREFIX"])
ffmpeg_prefix = Path(os.environ["PIXELKIT_FFMPEG_PREFIX"])
icon = Path(os.environ["PIXELKIT_MACOS_ICON"])
metadata = icon.parent / "magick-metadata"
version = os.environ["PIXELKIT_VERSION"]
identity = os.environ.get("PIXELKIT_CODESIGN_IDENTITY") or None

binaries = [(str(prefix / "bin" / "magick"), "imagemagick/bin")]
binaries.extend((str(ffmpeg_prefix / "bin" / name), "ffmpeg/bin") for name in ("ffmpeg", "ffprobe"))
datas = [(str(project / "src" / "pixelkit" / "assets"), "pixelkit/assets")]
datas.append((str(icon.parent / "ffmpeg-build.txt"), "licenses/FFmpeg"))
for pattern in ("LICENSE*", "COPYING*", "NOTICE*"):
    for source in sorted(ffmpeg_prefix.glob(pattern)):
        if source.is_file():
            datas.append((str(source), "licenses/FFmpeg"))
# Explicitly collect dynamically loaded coders and filters. PyInstaller follows
# their dylib dependencies and rewrites load paths for relocation inside .app.
for source in sorted((prefix / "lib").rglob("*")):
    if not source.is_file() or source.is_symlink():
        continue
    destination = str(Path("imagemagick") / source.relative_to(prefix).parent)
    if source.suffix in {".so", ".dylib"}:
        binaries.append((str(source), destination))
    elif source.suffix == ".xml":
        datas.append((str(source), destination))
    elif source.suffix == ".la" and source.parent.name in {"coders", "filters"}:
        # libltdl requires these descriptors to open the .so modules. Strip
        # build-machine paths so it loads modules beside the descriptor.
        target = metadata / source.relative_to(prefix)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = re.sub(r"^(dependency_libs|libdir)=.*$", r"\1=''", source.read_text(), flags=re.MULTILINE)
        target.write_text(descriptor)
        datas.append((str(target), destination))
datas.append((str(prefix / "etc"), "imagemagick/etc"))
for name in ("LICENSE", "NOTICE"):
    if (prefix / name).is_file():
        datas.append((str(prefix / name), "licenses/ImageMagick"))

a = Analysis(
    [str(project / "src" / "pixelkit" / "__main__.py")],
    pathex=[str(project / "src")],
    binaries=binaries,
    datas=datas,
    excludes=["tkinter"],
    noarchive=False,
)
# Retain license notices from bundled Homebrew dependencies as well.
license_roots = set()
for _destination, source, _kind in a.binaries:
    resolved = Path(source).resolve()
    if "Cellar" in resolved.parts:
        index = resolved.parts.index("Cellar")
        license_roots.add(Path(*resolved.parts[:index + 3]))
for root in sorted(license_roots):
    for pattern in ("LICENSE*", "COPYING*", "NOTICE*"):
        for source in sorted(root.glob(pattern)):
            if source.is_file():
                destination = str(Path("licenses") / root.parent.name / source.name)
                a.datas.append((destination, str(source), "DATA"))

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PixelKit",
    console=False,
    argv_emulation=False,  # Qt handles QFileOpenEvent itself.
    target_arch=os.environ["PIXELKIT_ARCH"],
    codesign_identity=identity,
    strip=False,
    upx=False,
)
collection = COLLECT(exe, a.binaries, a.datas, name="PixelKit", strip=False, upx=False)
app = BUNDLE(
    collection,
    name="PixelKit.app",
    icon=str(icon),
    bundle_identifier="com.pixelkit.desktop",
    info_plist={
        "CFBundleDisplayName": "PixelKit",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": os.environ["PIXELKIT_MACOS_MIN_VERSION"],
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "LSApplicationCategoryType": "public.app-category.graphics-design",
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "Images",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "CFBundleTypeExtensions": ["jpg", "jpeg", "png", "webp", "avif", "gif", "bmp", "tif", "tiff", "heic", "heif", "ico"],
        }, {
            "CFBundleTypeName": "Videos",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "CFBundleTypeExtensions": ["mp4", "mov", "m4v"],
        }],
    },
)
