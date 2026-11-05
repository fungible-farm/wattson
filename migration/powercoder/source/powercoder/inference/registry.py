"""Model registry for the NEM PowerCoder mission.

Tracks teacher and student models with their roles, revisions, and deployment status.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml


@dataclass
class ModelEntry:
    """A registered model."""

    id: str
    name: str
    role: str
    architecture: str
    hf_id: str
    revision: Optional[str]
    license: str
    training_framework: Optional[str]
    deployment: Optional[list[dict[str, Any]]]
    notes: Optional[str]


class ModelRegistry:
    """Registry of teacher and student models."""

    def __init__(self, path: str = "configs/powercoder/models.yaml"):
        self.path = Path(path)
        self.models: dict[str, ModelEntry] = {}
        self.load()

    def load(self):
        """Load the model registry from YAML."""
        if self.path.exists():
            with open(self.path) as f:
                data = yaml.safe_load(f)
            for entry in data.get("models", []):
                model = ModelEntry(
                    id=entry["id"],
                    name=entry["name"],
                    role=entry["role"],
                    architecture=entry["architecture"],
                    hf_id=entry["hf_id"],
                    revision=entry.get("revision"),
                    license=entry["license"],
                    training_framework=entry.get("training_framework"),
                    deployment=entry.get("deployment"),
                    notes=entry.get("notes"),
                )
                self.models[model.id] = model

    def save(self):
        """Save the model registry to YAML."""
        data = {"models": []}
        for model in self.models.values():
            data["models"].append(
                {
                    "id": model.id,
                    "name": model.name,
                    "role": model.role,
                    "architecture": model.architecture,
                    "hf_id": model.hf_id,
                    "revision": model.revision,
                    "license": model.license,
                    "training_framework": model.training_framework,
                    "deployment": model.deployment,
                    "notes": model.notes,
                }
            )
        with open(self.path, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False)

    def get(self, model_id: str) -> Optional[ModelEntry]:
        """Get a model by ID."""
        return self.models.get(model_id)

    def list_by_role(self, role: str) -> list[ModelEntry]:
        """List models by role."""
        return [m for m in self.models.values() if m.role == role]

    def update_revision(self, model_id: str, revision: str):
        """Update the pinned revision for a model."""
        if model_id in self.models:
            self.models[model_id].revision = revision
            self.save()
