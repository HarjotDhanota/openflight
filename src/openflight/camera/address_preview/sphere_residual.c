#include <math.h>
#include <stddef.h>

static double clip(double x, double low, double high) {
    return fmin(fmax(x, low), high);
}

void sphere_geometry(const double *p, const double *back,
                     double *a, double *b, double *base) {
    double st = sin(p[4]), ct = cos(p[4]), ca = cos(p[3]), sa = sin(p[3]);
    for (size_t row = 0; row < 60; ++row) {
        for (size_t col = 0; col < 60; ++col) {
            size_t i = row * 60 + col;
            double nx = (2.0 * col + .5 - p[0]) / p[2];
            double ny = (2.0 * row + .5 - p[1]) / p[2];
            double rho = sqrt(nx * nx + ny * ny);
            double nz = sqrt(fmax(1.0 - rho * rho, 0));
            double lit = fmax(st * (ca * nx + sa * ny) + ct * nz, 0);
            double cover = clip((1.0 - rho) * p[2] / 2.0 + .5, 0, 1);
            a[i] = cover; b[i] = cover * lit; base[i] = (1.0 - cover) * back[i];
        }
    }
}

void conditional_residual(const double *a, const double *b, const double *base,
                          const double *observed, size_t n, double scale,
                          double *weights, double *residual, double *coefficients) {
    for (size_t i = 0; i < n; ++i) weights[i] = 1;
    double ambient = 0, diffuse = 0;
    for (size_t pass = 0; pass < 3; ++pass) {
        double aa = 0, bb = 0, ab = 0, av = 0, bv = 0;
        for (size_t i = 0; i < n; ++i) {
            double wa = weights[i] * a[i], wb = weights[i] * b[i];
            double value = observed[i] - base[i];
            aa += wa * a[i]; bb += wb * b[i]; ab += wa * b[i];
            av += wa * value; bv += wb * value;
        }
        aa += 1e-8; bb += 1e-8;
        double denominator = fmax(aa * bb - ab * ab, 1e-8);
        ambient = clip((av * bb - bv * ab) / denominator, 0, 255);
        diffuse = clip((bv * aa - av * ab) / denominator, 0, 400);
        for (size_t i = 0; i < n; ++i) {
            residual[i] = clip(base[i] + ambient * a[i] + diffuse * b[i], 0, 255) - observed[i];
            weights[i] = fmin(1.0, scale / fmax(fabs(residual[i]), 1e-8));
        }
    }
    coefficients[0] = ambient; coefficients[1] = diffuse;
}
