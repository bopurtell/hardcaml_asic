#!/usr/bin/env python3
"""Deprecated SOURCE-only runner/collector reference command.

Imports the retained checkout oracle in `flow/hardcaml_asic_flow/coarse_flow_oracle.py`.
Production `hardcaml-asic-flow preflight|run|postcheck|collect` is native OCaml;
this historical command is not the same public implementation or installed API.
"""

import sys
from pathlib import Path

# Explicit checkout reference access; production never searches this source path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "flow"))

from hardcaml_asic_flow import coarse_flow_oracle  # noqa: E402


if __name__ == "__main__":
    sys.exit(coarse_flow_oracle.main())
