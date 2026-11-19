"""Static reading of CMake, Conan and vcpkg manifests. CMake is never run to learn about a project.

Running `cmake` executes the project's scripts (`execute_process`, custom commands, downloads), so
everything here is regex parsing of the text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.adapters.models import Dependency

BUILD_FILES = {
    "CMakeLists.txt": "CMake",
    "Makefile": "Make",
    "makefile": "Make",
    "GNUmakefile": "Make",
    "meson.build": "Meson",
    "configure.ac": "Autotools",
    "BUILD": "Bazel",
    "BUILD.bazel": "Bazel",
    "WORKSPACE": "Bazel",
    "MODULE.bazel": "Bazel",
    "SConstruct": "SCons",
}
LOCKFILES = {"conan.lock"}
FRAMEWORKS = {
    "gtest": "GoogleTest",
    "googletest": "GoogleTest",
    "gmock": "GoogleTest",
    "catch2": "Catch2",
    "catch": "Catch2",
    "doctest": "doctest",
    "boost": "Boost",
    "qt5": "Qt",
    "qt6": "Qt",
    "qt": "Qt",
    "fmt": "fmt",
    "spdlog": "spdlog",
    "eigen3": "Eigen",
    "eigen": "Eigen",
    "opencv": "OpenCV",
    "protobuf": "Protobuf",
    "grpc": "gRPC",
    "openssl": "OpenSSL",
    "curl": "libcurl",
    "nlohmann_json": "nlohmann/json",
    "sdl2": "SDL2",
}
_EXACT_VERSION = re.compile(r"^v?\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.]+)?$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_COMMENT = re.compile(r"#.*")
_COMMAND = re.compile(
    r"(?im)^[ \t]*(?P<name>[a-z_][a-z0-9_]*)[ \t]*\((?P<body>[^()]*(?:\([^()]*\)[^()]*)*)\)"
)


def strip_cmake_comments(text: str) -> str:
    return "\n".join(
        _COMMENT.sub("", line) if '"' not in line else line for line in text.splitlines()
    )


def _commands(text: str) -> list[tuple[str, str]]:
    return [
        (m.group("name").lower(), m.group("body"))
        for m in _COMMAND.finditer(strip_cmake_comments(text))
    ]


def _tokens(body: str) -> list[str]:
    return [t.strip('"') for t in re.findall(r'"[^"]*"|[^\s"]+', body)]


@dataclass
class CMakeInfo:
    dependencies: list[Dependency] = field(default_factory=list)
    ctest_enabled: bool = False
    ctest_tests: int = 0
    test_discovery: bool = False  # gtest_discover_tests() and friends: the count is unknown
    executables: int = 0
    libraries: int = 0
    runs_commands: set[str] = field(default_factory=set)
    min_version: str | None = None


def parse_cmake(text: str, rel: str) -> CMakeInfo:
    info = CMakeInfo()
    for name, body in _commands(text):
        tokens = _tokens(body)
        if name == "cmake_minimum_required":
            match = re.search(r"VERSION\s+([\d.]+)", body, re.I)
            info.min_version = match.group(1) if match else None
        elif name in ("enable_testing",) or (name == "include" and tokens[:1] == ["CTest"]):
            info.ctest_enabled = True
        elif name == "add_test":
            info.ctest_tests += 1
        elif name in (
            "gtest_discover_tests",
            "gtest_add_tests",
            "catch_discover_tests",
            "doctest_discover_tests",
        ):
            info.test_discovery = True
        elif name == "add_executable":
            info.executables += 1
        elif name in ("add_library",):
            info.libraries += 1
        elif name == "find_package" and tokens:
            version = next((t for t in tokens[1:] if re.match(r"^\d+(?:\.\d+)*$", t)), "")
            exact = "EXACT" in tokens and bool(version)
            info.dependencies.append(
                Dependency(
                    name=tokens[0],
                    specifier=version,
                    pinned_version=version if exact else None,
                    source_file=rel,
                )
            )
        elif name == "fetchcontent_declare" and tokens:
            info.dependencies.append(_fetched(tokens, rel))
            info.runs_commands.add("downloads dependencies at configure time (FetchContent)")
        elif name == "externalproject_add" and tokens:
            info.dependencies.append(_fetched(tokens, rel))
            info.runs_commands.add("downloads dependencies at build time (ExternalProject)")
        elif name == "execute_process":
            info.runs_commands.add("runs commands at configure time (execute_process)")
        elif name == "file" and tokens[:1] and tokens[0].upper() in ("DOWNLOAD", "UPLOAD"):
            info.runs_commands.add("uses the network at configure time (file(DOWNLOAD))")
        elif name in ("add_custom_command", "add_custom_target") and "COMMAND" in tokens:
            info.runs_commands.add("runs custom commands during the build")
    return info


def _fetched(tokens: list[str], rel: str) -> Dependency:
    def after(key: str) -> str | None:
        return (
            tokens[tokens.index(key) + 1]
            if key in tokens and tokens.index(key) + 1 < len(tokens)
            else None
        )

    tag = after("GIT_TAG")
    url = after("URL")
    pinned = None
    if tag and (_SHA.match(tag) or _EXACT_VERSION.match(tag) or re.match(r"^release-\d", tag)):
        pinned = tag
    elif url and "URL_HASH" in tokens:
        pinned = "hash-verified"
    spec = tag or ("URL" if url else "")
    return Dependency(name=tokens[0], specifier=spec, pinned_version=pinned, source_file=rel)


def parse_conan(text: str, rel: str) -> list[Dependency]:
    deps: list[Dependency] = []
    in_requires = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_requires = line.lower() == "[requires]"
        elif in_requires and line and not line.startswith("#"):
            name, _, version = line.partition("/")
            version = version.split("@")[0].strip()
            exact = bool(version) and not re.search(r"[\[\]<>~^*]", version)
            deps.append(
                Dependency(
                    name=name.strip(),
                    specifier=version,
                    pinned_version=version if exact else None,
                    source_file=rel,
                )
            )
    return deps


def parse_vcpkg(text: str, rel: str) -> list[Dependency]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    overrides = {
        o.get("name"): o.get("version") for o in data.get("overrides", []) if isinstance(o, dict)
    }
    deps: list[Dependency] = []
    for entry in data.get("dependencies", []):
        name = (
            entry
            if isinstance(entry, str)
            else (entry.get("name") if isinstance(entry, dict) else None)
        )
        if not name:
            continue
        version = overrides.get(name)
        deps.append(
            Dependency(
                name=str(name),
                specifier=str(version or ""),
                pinned_version=str(version) if version else None,
                source_file=rel,
            )
        )
    return deps


def read_build_files(root: Path, files: list[str]) -> tuple[CMakeInfo, list[Dependency], list[str]]:
    """Parse every CMake, Conan and vcpkg file. Returns (cmake info, other deps, parse errors)."""
    merged = CMakeInfo()
    others: list[Dependency] = []
    errors: list[str] = []
    for rel in files:
        path = root / rel
        if path.is_symlink() or path.stat().st_size > 1_000_000:
            continue
        name = path.name
        text = path.read_text(encoding="utf-8", errors="replace")
        if name == "CMakeLists.txt" or name.endswith(".cmake"):
            one = parse_cmake(text, rel)
            merged.dependencies += one.dependencies
            merged.ctest_enabled |= one.ctest_enabled
            merged.ctest_tests += one.ctest_tests
            merged.test_discovery |= one.test_discovery
            merged.executables += one.executables
            merged.libraries += one.libraries
            merged.runs_commands |= one.runs_commands
            merged.min_version = merged.min_version or one.min_version
        elif name in ("conanfile.txt",):
            others += parse_conan(text, rel)
        elif name == "vcpkg.json":
            parsed = parse_vcpkg(text, rel)
            if not parsed and text.strip() and not text.strip().startswith("{"):
                errors.append(f"{rel}: not valid JSON")
            others += parsed
    return merged, others, errors
