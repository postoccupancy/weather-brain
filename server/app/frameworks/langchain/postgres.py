"""Connect LangChain to existing vector tables without schema setup."""
from langchain_postgres import PGVector


class ExistingPGVector(PGVector):
    def create_tables_if_not_exists(self) -> None:
        pass

    async def acreate_tables_if_not_exists(self) -> None:
        pass

    def create_collection(self) -> None:
        with self._make_sync_session() as session:
            if self.get_collection(session) is None:
                raise RuntimeError(f"Existing vector collection not found: {self.collection_name}")

    async def acreate_collection(self) -> None:
        async with self._make_async_session() as session:
            if await self.aget_collection(session) is None:
                raise RuntimeError(f"Existing vector collection not found: {self.collection_name}")
