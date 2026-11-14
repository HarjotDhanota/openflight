"""Print the generic outline's point order (mm from the toe, mm above the sole) to locate the hosel stub."""
import numpy as np
import geom
uv = geom.OUTLINE
x, y = uv[:, 0].max() - uv[:, 0], uv[:, 1] - uv[:, 1].min()
for i in list(range(0, len(uv), 6)) + [len(uv) - 1]:
    print("%3d  x %6.1f  y %5.1f" % (i, x[i], y[i]))
