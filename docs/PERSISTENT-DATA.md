# Persistent Data and kernel 18

Kernel 17's eight-core and one-core images both produce
`AMFI: failed to create OSDictionary copy: 14` at `OSEntitlements.cpp:827`.
The owner's photo maps to `IOVnodeHasEntitlement` in `exec_mach_imgact`, called through
`posix_spawn`. A userspace process has reached child execution. The photo does not
identify that process or show the preceding root/SystemOS mount lines.

The 22H384 AMFI converter returns private status 14 when conversion produces no
object. This is not evidence that `/var` caused the failure. Kernel 17 had no Data
seed or writable `/private/var` mount, which is a separate missing startup input.

## Entitlement query

The OS profile's `native-entitlements-vnode-query.patch` changes single-key vnode
queries to AMFI's `copyEntitlementAsOSObject` callback, the interface already used
by Apple's task-based `IOUserClient::copyClientEntitlement`. It uses the same
cached signed `cs_blob`, architecture offset and OSEntitlements context. The
returned object is retained; false Boolean values and absent keys retain their
previous meaning. Missing providers and conversion errors fail closed.

Read-only inspection of the original 22H384 AMFI implementation confirms that
this callback selects the requested key before conversion. The failing old path
materializes the entire dictionary. This addresses the photographed call path;
other callers that request complete dictionaries still need hardware observation.
`native-entitlements-diag=1` logs the executable name, key, callback result and
CDHash prefix, plus bounded factory diagnostics if a whole-dictionary copy occurs.
AMFI policy, trust caches and native signature validation remain active.

## Seed and storage

`scripts/prepare-blueline-data.py` runs on the build Mac. It mounts the original
22H384 OS read-only, verifies its UUID and launchd hash, and copies the original
initial `/private/var` contents: 41 regular files, 637239 bytes, with file hashes
checked. It adds empty Data directories from upstream's fixture workflow. It
does not add activation preferences, keys or user records. The existing root
image stays in place.

| Input | Value |
| --- | --- |
| Data volume UUID | `789552B2-C2BA-476F-BE80-99D2ECA02A7C` |
| Logical size | 80 GiB |
| Packed seed size | 1093760 bytes |
| Encoded seed SHA256 | `e3b1ea02c0baea7e1c085b6734178b23966e998c26b256cb1423416a5d1835fd` |
| Logical seed identity | `023d33e855bd5c9894d5e7cbd5247ea1bb9b88797137245648e4602877523c57` |
| Store first / last LBA | `0x98347f` / `0x1d836ff`, 4096-byte blocks |
| Store blocks | 20972161, including header and bitmap |
| Tail reserve | 1024 MiB |

Upstream RamBlock's persistent overlay writes only within NativeUfs's validated
store window inside the measured `darwin` GPT partition. It excludes the root
image, its guard, Android userdata and the partition's final GiB. FUA and cache
synchronization retain upstream write-order behavior. The original seed is
immutable; changed logical blocks are read from the overlay.

The first boot initializes an overlay with this seed identity. Later boots reuse
it with the same seed, size and reserve. Keep the seed stable across kernel builds:
upstream treats a changed identity or geometry as a new store. Normal arguments
contain no reset option.

`XNU_DEBUG_DATA_MOUNT=1` is a separate development-only gate. It requires explicit
`debug-data-mount=1`, `debug-rootauth-bypass=1`, RamBlock and a writable persistent
store. The authenticated owner mount keeps its existing manifest requirements.
The reused upstream mount helper checks the Data UUID, actual device, APFS and
writable `/private/var`. It refuses failed or volatile overlays. Fresh state is 3;
resumed writable state is 2. Boot arguments also include `-apfs_shared_datavolume`,
`sheng-data-persist=2` and `sheng-log-reserve=1024`. The reserve alone does not
enable persistent diagnostic logging.

## Reproduction and verification

On the build Mac, after sourcing `~/blueline/env.sh`:

```sh
python3 ../UniXNU-blueline/scripts/prepare-blueline-data.py \
  --unixnu "$PWD" --output ~/blueline/ios18-tools/blueline-data --gib 80
./unixnu defconfig blueline_defconfig -D ../UniXNU-blueline
./unixnu build kernel
./unixnu build payload --kernel out/blueline/ipados-18.7.11-22H384/blueline/kernel/kernel.macho \
  --uefi ~/blueline/uefi-font \
  --trust-cache ~/blueline/ios18-tools/trust/22H384-static-system.bundle \
  --data-ramdisk ~/blueline/ios18-tools/blueline-data/blueline-data.dbr
```

Seed creation refuses to overwrite an existing directory. For subsequent builds,
reuse its `blueline-data.dbr`. Package on Windows as described in README; no root
reflash is needed.

Host checks cover the actual mount helper and per-key query with API fault
injection under AddressSanitizer/UndefinedBehaviorSanitizer. RamBlock tests cover
restart, torn writes, flush failures, immutable seeds and large packed disks. The
22H384 binding audit checks 190 virtual methods, 24 overrides and zero unresolved
original imports. These checks do not establish phone persistence.

On the phone, capture `Data: persistent state`, `Data: /private/var mount status`
and `AMFI DIAG key` lines. Successful mount requires status 0 and readonly 0.
After reboot, state 2 confirms reopening the same overlay; survival of an actual
file write still needs verification. Touch/buttons and GPU remain separate
unverified hardware work.
