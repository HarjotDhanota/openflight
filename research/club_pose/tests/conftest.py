import pathlib
import sys

# Put research/ on sys.path so `import club_pose` resolves while the package
# lives outside src/openflight/. Internal modules use relative imports, so the
# club_pose/types.py module never shadows stdlib `types`.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
