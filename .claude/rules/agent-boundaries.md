# Rule: an agent authors only its own document

Governs the agents and skills under `plugins/`. An agent never authors JSON that
belongs to another agent's responsibility; it asks the agent that owns the document.
