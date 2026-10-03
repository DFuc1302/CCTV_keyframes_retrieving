"""CCTV-specific ingestion, extending the legacy frame pipeline."""

import os
import sys

# Windows native BLAS/OpenMP defaults reserve large thread stacks against system
# commit even while idle. Set bounded defaults before NumPy/FAISS/Torch imports;
# explicit operator overrides remain authoritative.
if sys.platform == "win32":
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
    os.environ.setdefault("OMP_NUM_THREADS", "2")
