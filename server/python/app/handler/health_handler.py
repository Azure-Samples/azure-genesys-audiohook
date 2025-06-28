import asyncio
import logging
from typing import Optional

from azure.storage.blob.aio import BlobServiceClient

from ..storage.base_conversation_store import ConversationStore

from ..models import (
    Error,
    HealthCheckResponse,
)
from ..storage.in_memory_conversation_store import (
    InMemoryConversationStore,
)

from ..events.event_publisher import EventPublisher

class HealthHandler:

    def __init__(
        self,
        conversations_store: ConversationStore,
        blob_service_client: Optional[BlobServiceClient],
        event_publisher: Optional[EventPublisher],
        logger: logging.Logger,
    ):
        self.conversations_store = conversations_store
        self.blob_service_client = blob_service_client
        self.event_publisher = event_publisher
        self.logger = logger

    async def health_check(self):
        """
        Health check endpoint

        https://learn.microsoft.com/en-us/azure/container-apps/health-probes
        """

        # TODO abstract this to a health check class

        # Check conversations store (CosmosDB or in-memory)
        try:
            # InMemoryConversationStore is always healthy
            if isinstance(self.conversations_store, InMemoryConversationStore):
                pass
            else:
                # Try a simple list operation (should raise if CosmosDB is unreachable or misconfigured)
                await asyncio.wait_for(
                    self.conversations_store.list(active=None), timeout=5
                )
        except Exception as e:
            self.logger.error(
                f"Health check failed: Conversations store unhealthy: {e}"
            )

            return HealthCheckResponse(
                status="unhealthy",
                error=Error(
                    code="conversations_store",
                    message=f"Conversations store is unhealthy. {str(e)}.",
                ),
            ).model_dump(), 500

        # Check Azure Blob Storage (if configured)
        if self.blob_service_client:
            try:
                # get_service_properties is a lightweight call
                await asyncio.wait_for(
                    self.blob_service_client.get_service_properties(), timeout=5
                )
            except Exception as e:
                self.logger.error(f"Health check failed: Blob Storage unhealthy: {e}")

                return HealthCheckResponse(
                    status="unhealthy",
                    error=Error(
                        code="blob_storage",
                        message=f"Blob storage is unhealthy. {str(e)}.",
                    ),
                ).model_dump(), 500

        # Check Azure Event Hub (if configured)
        if self.event_publisher:
            try:
                # Try to create a batch (does not send, but checks connection/permissions)
                await asyncio.wait_for(
                    self.event_publisher.producer_client.create_batch(), timeout=5
                )
            except Exception as e:
                self.logger.error(f"Health check failed: Event Hub unhealthy: {e}")

                return HealthCheckResponse(
                    status="unhealthy",
                    error=Error(
                        code="event_hub",
                        message=f"Event Hub is unhealthy. {str(e)}.",
                    ),
                ).model_dump(), 500

        # TODO check Azure Speech Service (if configured)

        return HealthCheckResponse(status="healthy").model_dump(exclude_none=True), 200