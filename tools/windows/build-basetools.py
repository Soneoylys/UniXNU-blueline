#!/usr/bin/env python3
"""Build the EDK II BaseTools C programs a Mu-Silicium firmware volume needs, on Windows, with zig's clang.

    python tools/windows/build-basetools.py EDK2_WIN_DIR [TOOL ...]

EDK2_WIN_DIR is a directory set up by UniXNU-pocket-max tools/windows/setup-edk2.py (mu_basecore/, bin/,
llvm/); that script builds GenFw only. This one reads each tool's GNUmakefile (APPNAME, OBJECTS) and
compiles the listed sources against the BaseTools Common library (an archive, as in the GNU build, so tool
sources may override its symbols) into bin/<APPNAME>.exe.
Default tools: GenFv GenFfs GenSec GenCrc32 LzmaCompress VolInfo EfiRom TianoCompress BrotliCompress VfrCompile.
(pip install ziglang first.)
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT = ["GenFv", "GenFfs", "GenSec", "GenCrc32", "LzmaCompress", "VolInfo", "EfiRom", "TianoCompress",
           "BrotliCompress", "VfrCompile"]


def makefile_vars(path):
    text = path.read_text().replace("\\\n", " ")
    out = {}
    for m in re.finditer(r"^\s*([A-Z_]+)\s*[:+]?=\s*(.*)$", text, re.M):
        key, value = m.group(1), m.group(2).strip()
        out[key] = (out.get(key, "") + " " + value).strip() if "+=" in m.group(0) else value
    return out


def expand(value, variables):
    return re.sub(r"\$\(([A-Z_]+)\)", lambda m: variables.get(m.group(1), ""), value)


def build_vfrcompile(root, c, cc, lib, includes):
    """VfrCompile: PCCTS antlr and dlg first, then the generated parser and lexer, then the C++ sources."""
    src = c / "VfrCompile"
    pccts = src / "Pccts"
    work = root / "obj" / "VfrCompile"
    work.mkdir(parents=True, exist_ok=True)
    pflags = ["-O2", f"-I{pccts / 'support/set'}", f"-I{pccts / 'h'}", "-DUSER_ZZSYN", "-DZZLEXBUFSIZE=65536", "-std=gnu11"]
    tools = {
        "antlr": ["antlr", "scan", "err", "bits", "build", "fset2", "fset", "gen", "globals", "hash", "lex", "main", "misc",
                  "pred", "egman", "mrhoist", "fcache"],
        "dlg": ["dlg_p", "dlg_a", "main", "err", "support", "output", "relabel", "automata"],
    }
    for tool, names in tools.items():
        sources = [str(pccts / tool / f"{n}.c") for n in names] + [str(pccts / "support/set/set.c")]
        subprocess.run([*cc, f"-I{pccts / tool}", *pflags, "-o", str(work / f"{tool}.exe"), *sources], check=True)
    # Generated sources go to the work directory, not into the edk2 checkout. antlr derives output names from
    # the grammar path, so it runs on a copy with relative names.
    shutil.copyfile(src / "VfrSyntax.g", work / "VfrSyntax.g")
    subprocess.run([str(work / "antlr.exe"), "-CC", "-e3", "-ck", "3", "-k", "2", "-fl", "VfrParser.dlg", "-ft", "VfrTokens.h",
                    "-o", ".", "VfrSyntax.g"], check=True, cwd=work)
    subprocess.run([str(work / "dlg.exe"), "-C2", "-i", "-CC", "-cl", "VfrLexer", "-o", ".", "VfrParser.dlg"],
                   check=True, cwd=work)
    cxx = [*cc[:3], "c++", *cc[4:], "-std=c++14", "-DPCCTS_USE_NAMESPACE_STD", *includes, f"-I{src}", f"-I{pccts / 'h'}",
           f"-I{work}"]
    sources = [pccts / "h/AParser.cpp", pccts / "h/DLexerBase.cpp", pccts / "h/ATokenBuffer.cpp",
               work / "EfiVfrParser.cpp", work / "VfrLexer.cpp", work / "VfrSyntax.cpp",
               src / "VfrFormPkg.cpp", src / "VfrError.cpp", src / "VfrUtilityLib.cpp", src / "VfrCompiler.cpp"]
    print(f"VfrCompile: {len(sources)} sources")
    subprocess.run([*cxx, "-o", str(root / "bin" / "VfrCompile.exe"), *map(str, sources), str(lib), "-lrpcrt4"], check=True)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    root = Path(sys.argv[1]).resolve()
    tools = sys.argv[2:] or DEFAULT
    edk2 = root / "mu_basecore"
    c = edk2 / "BaseTools/Source/C"
    common = [c / "Common" / (o[:-2] + ".c") for o in (c / "Common/GNUmakefile").read_text().split() if o.endswith(".o")]
    includes = [f"-I{c / d}" for d in ("Include", "Include/X64", "Include/Common", "Common")]
    includes += [f"-I{edk2 / 'MdePkg/Include'}", f"-I{edk2 / 'MdePkg/Include/X64'}"]
    zig = [sys.executable, "-m", "ziglang"]
    # USE_MYALLOC=0: the debug allocator in Common/MyAlloc.c clashes with the LZMA SDK's MyAlloc under lld-link.
    cc = [*zig, "cc", "-target", "x86_64-windows-gnu", "-O2", "-Wno-everything", "-DUSE_MYALLOC=0"]
    objdir = root / "obj" / "Common"
    objdir.mkdir(parents=True, exist_ok=True)
    objs = []
    for source in common:
        obj = objdir / (source.stem + ".o")
        subprocess.run([*cc, *includes, "-c", str(source), "-o", str(obj)], check=True)
        objs.append(obj)
    lib = root / "obj" / "libCommon.a"
    lib.unlink(missing_ok=True)
    subprocess.run([*zig, "ar", "rcs", str(lib), *map(str, objs)], check=True)
    for name in tools:
        if name == "VfrCompile":
            build_vfrcompile(root, c, cc, lib, includes)
            continue
        src = c / name
        variables = makefile_vars(src / "GNUmakefile")
        app = variables.get("APPNAME", name)
        objects = expand(variables.get("OBJECTS", ""), variables).split()
        sources = []
        for obj in objects:
            base = src / obj[:-2]
            for ext in (".c", ".cpp", ".cc"):
                if base.with_suffix(ext).is_file() or Path(str(base) + ext).is_file():
                    sources.append(Path(str(base) + ext))
                    break
            else:
                sys.exit(f"{name}: no source for {obj}")
        extra = []
        if name == "BrotliCompress":
            brotli = src / "brotli/c"
            extra = [f"-I{brotli / 'include'}"]
        out = root / "bin" / f"{app}.exe"
        flags = [f for f in variables.get("CFLAGS", "").split() if f.startswith(("-D", "-I"))]
        flags = [f"-I{src / f[2:]}" if f.startswith("-I") and not Path(f[2:]).is_absolute() else f for f in flags]
        cmd = [*cc, *includes, f"-I{src}", *extra, *flags, "-o", str(out), *map(str, sources), str(lib), "-lrpcrt4"]
        print(f"{app}: {len(sources)} sources")
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
