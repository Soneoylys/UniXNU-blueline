#!/usr/bin/env python3
"""Trust bundle for the blueline GPU candidate: one owner module with the CDHashes of the Adreno Metal plugin, its probe
daemon and Turnip (build/adreno-metal-plugin/trust.json), then the two original 22H384 Apple modules byte for byte. The
22H384 counterpart of UniXNU scripts/make-owner-trust-bundle.py (which is tied to the 21H461 bundle).
Usage: make-blueline-trust-bundle.py ORIGINAL_22H384_BUNDLE TRUST_JSON OUTPUT"""
import hashlib, json, struct, sys, uuid
from pathlib import Path

ORIGINAL_SHA = '26ed8386ab1984442d2e65679bc4ed4544ba8a71dcfccdecaf39e5ceb6ef7ce0'   # ~/blueline/ios18-tools/trust
original_path, trust_path, out = (Path(p) for p in sys.argv[1:4])
original = original_path.read_bytes()
if hashlib.sha256(original).hexdigest() != ORIGINAL_SHA:
    raise SystemExit('The 22H384 static/System trust bundle differs from the recorded input')
count = struct.unpack_from('<I', original)[0]
if count != 2:
    raise SystemExit('expected two original modules')
offsets = struct.unpack_from('<2I', original, 4)
originals = [original[offsets[0]:offsets[1]], original[offsets[1]:]]
trust = json.loads(trust_path.read_text())
# Ascending order: DarwinBoot's ValidTrustCaches and the kernel's binary search require it.
hashes = sorted({bytes.fromhex(trust[k]) for k in ('cdhash', 'daemon_cdhash', 'turnip_cdhash')})
if len(hashes) != 3:
    raise SystemExit('Duplicate CDHash')
identifier = uuid.uuid5(uuid.NAMESPACE_URL, 'org.blu-sharky.adreno-metal:' + ''.join(h.hex() for h in hashes))
own = struct.pack('<I16sI', 2, identifier.bytes, len(hashes)) + b''.join(h + bytes([2, 0, 0, 0]) for h in hashes)
modules = [own, *originals]
cursor, starts = 16, []
for module in modules:
    starts.append(cursor)
    cursor += len(module)
bundle = struct.pack('<4I', 3, *starts) + b''.join(modules)


def valid_like_the_loader(data):
    # DarwinBoot.c ValidTrustCaches, as make-owner-trust-bundle.py checks it.
    n = struct.unpack_from('<I', data)[0]
    if not 0 < n <= 128:
        return False
    for index in range(n):
        start = struct.unpack_from('<I', data, 4 + 4 * index)[0]
        end = struct.unpack_from('<I', data, 8 + 4 * index)[0] if index + 1 < n else len(data)
        if start < 4 + 4 * n or start & 3 or end > len(data) or end - start < 24 or struct.unpack_from('<I', data, start)[0] != 2:
            return False
        entries = struct.unpack_from('<I', data, start + 20)[0]
        if end - start != 24 + entries * 24:
            return False
        for entry in range(entries):
            at = start + 24 + entry * 24
            if not 1 <= data[at + 20] <= 4 or (entry and data[at - 24:at - 4] >= data[at:at + 20]):
                return False
    return True


if not valid_like_the_loader(bundle):
    raise SystemExit('The trust bundle would be refused by the boot loader')
out.write_bytes(bundle)
out.with_suffix('.json').write_text(json.dumps(dict(bundle_sha256=hashlib.sha256(bundle).hexdigest(),
    own_cdhashes=[h.hex() for h in hashes], original_bundle_sha256=ORIGINAL_SHA, original_modules_unchanged=True), indent=2) + '\n')
print('PASS: blueline trust bundle (owner module + both original 22H384 modules)', hashlib.sha256(bundle).hexdigest())
