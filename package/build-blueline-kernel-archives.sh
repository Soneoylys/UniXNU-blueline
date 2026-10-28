#!/bin/bash
# Build the two archives the blueline kernel links for the GPU candidate, on the build Mac:
#   build/adreno740/libAdreno740.a                    (XNU_NATIVE_ADRENO): the common NativeAdreno core with
#       kits/Sdm845Gpu (UniXNU-sdm845-common), the A630 bring-up, board policy, publication, firmware and the
#       staged AGXAccelerator
#   build/native-legacy-display/libNativeLegacyDisplay.late.a (XNU_NATIVE_LEGACY_DISPLAY): GopFramebuffer for 22H384
# Usage: build-blueline-kernel-archives.sh TREE KERNEL   (KERNEL: a built kernel of this profile, for the binding checks)
set -euo pipefail
TREE=$(cd "$1" && pwd); KERNEL=$2
U=$HOME/blueline/UniXNU; SOC=$HOME/blueline/UniXNU-sdm845-common; DEV=$HOME/blueline/UniXNU-blueline
python3 "$SOC/kits/Sdm845Gpu/build-native.py" --tree "$TREE" --components "$U"
python3 "$DEV/package/build-blueline-gpu.py" --tree "$TREE" --components "$U" --soc "$SOC" \
  --firmware "$DEV/.cache/firmware/blueline-gpu"
python3 "$DEV/package/build-blueline-accelerator.py" --tree "$TREE" --components "$U" --kernel "$KERNEL"
python3 "$DEV/package/build-blueline-display.py" --tree "$TREE" --components "$U" --kernel "$KERNEL" \
  --kernelcache "$U/out/ipsw/22H384/kernelcache.decompressed"
WORK=$(mktemp -d)
for a in "$TREE/build/sdm845-gpu-core/libSdm845GpuCore.a" "$TREE/build/blueline-gpu/libBluelineGpu.a" \
         "$TREE/build/blueline-accelerator/libBluelineAccelerator.a"; do
  d="$WORK/$(basename "$a" .a)"; mkdir -p "$d"; (cd "$d" && xcrun ar x "$a")
done
mkdir -p "$TREE/build/adreno740"
rm -f "$TREE/build/adreno740/libAdreno740.a"
xcrun ar rcs "$TREE/build/adreno740/libAdreno740.a" "$WORK"/*/*.o
rm -rf "$WORK"
echo "PASS: $TREE/build/adreno740/libAdreno740.a ($(xcrun ar t "$TREE/build/adreno740/libAdreno740.a" | wc -l | tr -d ' ') objects)"
