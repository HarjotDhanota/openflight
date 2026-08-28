"""Clubhead pose from the behind-ball camera and the radars.

Estimates clubhead position and orientation at impact by projecting a mesh of
the club into the camera and fitting it to the segmented silhouette, with range
and rotation axis constrained by the radars.

Not yet wired into the shot pipeline. See
docs/clubface-impact-location-report.md for what is validated and what is not.
"""
