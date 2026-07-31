# Apply shared ORM metadata constraints as soon as the backend package loads.
from . import model_constraints as model_constraints  # noqa: F401
from . import catalog_integrity as catalog_integrity  # noqa: F401
from . import payment_record_constraints as payment_record_constraints  # noqa: F401
from . import payment_event_constraints as payment_event_constraints  # noqa: F401
from . import webhook_delivery_constraints as webhook_delivery_constraints  # noqa: F401
