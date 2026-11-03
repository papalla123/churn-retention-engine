"""Configuración mínima en memoria para tests que necesitan variarla."""

RAW = {
    "weights": {"login_inactivity": 0.40, "support_pressure": 0.25, "usage_drop": 0.35},
    "ramps": {
        "login_inactivity": {"low": 7, "high": 45},
        "support_pressure": {"low": 0, "high": 5},
        "usage_drop": {"low": 10, "high": 70},
    },
    "risk_levels": {"alto_min": 70, "medio_min": 40},
}
