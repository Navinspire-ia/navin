"""Marks the suite as a package so ``from tests...`` imports resolve here.

Without this file, ``tests`` is a namespace package and loses to an installed
one: ``linkpreview`` 0.12.1 ships its own ``tests/`` into site-packages, which
shadowed this directory. The four provider modules that import
``tests.provider_test_utils`` then failed to collect, and a collection error
aborts the whole run - so a packaging accident in a third-party wheel was
silently taking the entire suite down with it.
"""
