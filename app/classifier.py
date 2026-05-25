"""
Compatibility pointer для B3 classifier path.

Runtime imports resolve в package app/classifier/__init__.py, потому
что T-024 требует app/classifier/rules.py. Public objects из T-005
экспортируются этим package: classify, ClassifierOutput, Finding.
"""

from app.classifier import ClassifierOutput, Finding, classify

__all__ = ["ClassifierOutput", "Finding", "classify"]
