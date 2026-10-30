"""Physical constants and unit conversions shared by the measurement and model code."""

# A golf ball's diameter as modern balls measure and the flight models were fitted with.
BALL_DIAMETER_MM = 42.7
BALL_DIAMETER_M = 0.0427
# Setup distances remain labelled defaults until observed or entered.
DEFAULT_MONITOR_TO_BALL_M = 2.0
DEFAULT_BALL_TO_NET_M = 11 * 0.3048
DEFAULT_NET_RANGE_M = DEFAULT_MONITOR_TO_BALL_M + DEFAULT_BALL_TO_NET_M
