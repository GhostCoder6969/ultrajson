"""Build the oomshim test helper (issue #712).

Test-only: not referenced by setup.py, never shipped. Run it from this
directory; it drops the built module next to the tests. setuptools picks
the right compiler and flags per platform (MSVC on Windows, -bundle on
macOS), same toolchain that builds ujson itself.
"""

from setuptools import Extension, setup

setup(
    name="oomshim-test-helper",
    ext_modules=[Extension("oomshim", sources=["oomshim.c"])],
    script_args=["build_ext", "--inplace"],
)
