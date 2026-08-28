import pathlib
import sys

# Put research/ on sys.path so `import silhouette_poc` resolves while the
# package lives outside src/openflight/.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
