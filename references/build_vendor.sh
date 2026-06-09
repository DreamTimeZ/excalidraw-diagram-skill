#!/usr/bin/env bash
# Rebuild references/vendor/ from pinned npm packages. End users never run this: the
# output is committed. Re-run only to upgrade the vendored versions (requires Node,
# pnpm and network), then commit the resulting vendor/ diff.
#
# Produces:
#   vendor/excalidraw.bundle.js            esbuild IIFE bundle of React + ReactDOM +
#                                          @excalidraw/excalidraw (CSS stripped), exposing
#                                          window.ExcalidrawLib
#   vendor/excalidraw.bundle.js.LEGAL.txt  third-party legal comments (esbuild-extracted)
#   vendor/fonts/<Family>/                 woff2 assets from the package dist/prod/fonts
#   vendor/licenses/                       React MIT, Excalidraw MIT, OFL-1.1, per-font
#                                          copyright notices
#   vendor/MANIFEST                        sha256 of every vendored file
set -euo pipefail

REACT_VERSION=19.2.7
EXCALIDRAW_VERSION=0.18.1
ESBUILD_VERSION=0.28.0

# Xiaolai (the automatic CJK fallback, 12 MB) is intentionally not vendored: CJK was
# never supported, and the renderer aborts when its subsets are requested instead of
# silently degrading. Assistant ships in the package but is excluded as dead weight:
# 0.18 assigns it no font-family id, and its only bundle reference is editor-UI CSS
# that the export path never mounts.
FONT_FAMILIES=(Cascadia ComicShanns Excalifont Liberation Lilita Nunito Virgil)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="$SCRIPT_DIR/vendor"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

cd "$BUILD_DIR"
printf '{ "name": "excalidraw-vendor-build", "private": true, "type": "module" }\n' > package.json
# esbuild's postinstall is only a binary-resolution optimization; the actual binary comes
# from its platform-specific optional dependency. Denying the build script keeps pnpm 11
# from failing the install with ERR_PNPM_IGNORED_BUILDS and runs no third-party scripts.
printf 'allowBuilds:\n  esbuild: false\n' > pnpm-workspace.yaml
pnpm add "react@$REACT_VERSION" "react-dom@$REACT_VERSION" \
    "@excalidraw/excalidraw@$EXCALIDRAW_VERSION" "esbuild@$ESBUILD_VERSION"

# The entry re-exports the whole library on the same global the old UMD build used, so
# render_template.html needs exactly one <script src>.
printf 'import * as ExcalidrawLib from "@excalidraw/excalidraw";\nwindow.ExcalidrawLib = ExcalidrawLib;\n' > entry.js

STAGE="$BUILD_DIR/vendor"
mkdir -p "$STAGE/fonts" "$STAGE/licenses"

# CSS is only needed by the interactive editor. exportToSvg inlines all styling.
pnpm exec esbuild entry.js --bundle --format=iife --minify \
    --define:process.env.NODE_ENV='"production"' \
    --loader:.css=empty \
    --legal-comments=linked \
    --outfile="$STAGE/excalidraw.bundle.js"

grep -q "exportToSvg" "$STAGE/excalidraw.bundle.js" \
    || { echo "ERROR: bundle smoke check failed (exportToSvg missing)" >&2; exit 1; }

FONTS_SRC="node_modules/@excalidraw/excalidraw/dist/prod/fonts"
for family in "${FONT_FAMILIES[@]}"; do
    cp -R "$FONTS_SRC/$family" "$STAGE/fonts/$family"
done

# React ships its MIT license in the package (identical text covers react-dom).
# Excalidraw and the fonts ship no license files in the npm package, so those texts
# are pinned here.
cp node_modules/react/LICENSE "$STAGE/licenses/React-LICENSE.txt"

cat > "$STAGE/licenses/Excalidraw-LICENSE.txt" <<'LICENSE_EOF'
MIT License

Copyright (c) 2020 Excalidraw

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
LICENSE_EOF

cat > "$STAGE/licenses/OFL-1.1.txt" <<'LICENSE_EOF'
SIL OPEN FONT LICENSE

Version 1.1 - 26 February 2007

PREAMBLE

The goals of the Open Font License (OFL) are to stimulate worldwide development of collaborative font projects, to support the font creation efforts of academic and linguistic communities, and to provide a free and open framework in which fonts may be shared and improved in partnership with others.

The OFL allows the licensed fonts to be used, studied, modified and redistributed freely as long as they are not sold by themselves. The fonts, including any derivative works, can be bundled, embedded, redistributed and/or sold with any software provided that any reserved names are not used by derivative works. The fonts and derivatives, however, cannot be released under any other type of license. The requirement for fonts to remain under this license does not apply to any document created using the fonts or their derivatives.

DEFINITIONS

"Font Software" refers to the set of files released by the Copyright Holder(s) under this license and clearly marked as such. This may include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the copyright statement(s).

"Original Version" refers to the collection of Font Software components as distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting, or substituting — in part or in whole — any of the components of the Original Version, by changing formats or by porting the Font Software to a new environment.

"Author" refers to any designer, engineer, programmer, technical writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS

Permission is hereby granted, free of charge, to any person obtaining a copy of the Font Software, to use, study, copy, merge, embed, modify, redistribute, and sell modified and unmodified copies of the Font Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components, in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled, redistributed and/or sold with any software, provided that each copy contains the above copyright notice and this license. These can be included either as stand-alone text files, human-readable headers or in the appropriate machine-readable metadata fields within text or binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font Name(s) unless explicit written permission is granted by the corresponding Copyright Holder. This restriction only applies to the primary font name as presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font Software shall not be used to promote, endorse or advertise any Modified Version, except to acknowledge the contribution(s) of the Copyright Holder(s) and the Author(s) or with their explicit written permission.

5) The Font Software, modified or unmodified, in part or in whole, must be distributed entirely under this license, and must not be distributed under any other license. The requirement for fonts to remain under this license does not apply to any document created using the Font Software.

TERMINATION

This license becomes null and void if any of the above conditions are not met.

DISCLAIMER

THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM OTHER DEALINGS IN THE FONT SOFTWARE.
LICENSE_EOF

cat > "$STAGE/licenses/FONTS.txt" <<'LICENSE_EOF'
Font assets under fonts/ are the subset woff2 builds shipped in the
@excalidraw/excalidraw npm package (dist/prod/fonts/). Copyright and license
information below comes from each binary's OpenType name table.

Cascadia Code
  SIL Open Font License 1.1 (see OFL-1.1.txt)
  Copyright (c) 2020 Microsoft Corporation
  https://github.com/microsoft/cascadia-code

ComicShanns
  MIT License
  Copyright (c) 2018 Shannon Miwa, (c) 2023 Jesus Gonzalez, (c) 2023 Rodrigo
  Batista de Moraes, (c) 2024 Fini Jastrow, (c) 2024 Kyle Beechly
  https://github.com/shannpersand/comic-shanns

Excalifont
  Copyright (c) 2024 Excalidraw. Distributed as part of the MIT-licensed
  Excalidraw project (see Excalidraw-LICENSE.txt).
  https://github.com/excalidraw/excalidraw

Liberation Sans
  SIL Open Font License 1.1 (see OFL-1.1.txt)
  Digitized data (c) 2007 Ascender Corporation
  https://github.com/liberationfonts/liberation-fonts

Lilita One
  SIL Open Font License 1.1 (see OFL-1.1.txt), Reserved Font Name "Lilita One"
  Copyright (c) 2011 Juan Montoreano
  https://fonts.google.com/specimen/Lilita+One

Nunito
  SIL Open Font License 1.1 (see OFL-1.1.txt)
  Copyright 2014 The Nunito Project Authors
  https://github.com/googlefonts/nunito

Virgil
  SIL Open Font License 1.1 (see OFL-1.1.txt)
  Copyright (c) 2011 Your Own Font Foundry
  https://virgil.excalidraw.com
LICENSE_EOF

{
    printf '# Provenance of references/vendor/. Generated by build_vendor.sh.\n'
    printf '# Every file below is hashed so a vendor change is always a reviewable hash\n'
    printf '# diff, never a silent blob swap.\n'
    printf '#\n'
    printf '# Upstream (npm, pinned in build_vendor.sh):\n'
    printf '#   react@%s + react-dom@%s + @excalidraw/excalidraw@%s\n' "$REACT_VERSION" "$REACT_VERSION" "$EXCALIDRAW_VERSION"
    printf '#   bundled into excalidraw.bundle.js with esbuild@%s (IIFE, CSS stripped)\n' "$ESBUILD_VERSION"
    printf '#   fonts/ from the package dist/prod/fonts (Xiaolai/CJK excluded)\n'
    printf '#\n'
    printf '# Verify integrity: cd references/vendor && shasum -a 256 -c MANIFEST\n'
    (cd "$STAGE" && find . -type f -not -name MANIFEST | sed 's|^\./||' | LC_ALL=C sort | xargs shasum -a 256)
} > "$STAGE/MANIFEST"

rm -rf "$VENDOR_DIR"
cp -R "$STAGE" "$VENDOR_DIR"
echo "OK: rebuilt $VENDOR_DIR (react@$REACT_VERSION, @excalidraw/excalidraw@$EXCALIDRAW_VERSION)"
