"""Business logic layer.

One module per use case. Services depend on `models`/`schemas` and on the
interfaces declared in `graph`, `ai`, and `integrations` - never on a
concrete adapter directly - so those subsystems can be swapped without
touching a service.
"""
