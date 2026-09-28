Создать модель генерации
class Generation(Base):
	id: PK
	prompt: str (Text)
	enriched_prompt: dict (EnrichedSongPrompt.model_dump, хз какой это тип в алхимии и постгрес, похоже JSONB)
	title: str (String)
	created_at: timestamp
	user_id: FK на User.tg_id
	user: relationship(User.generations)
	
class User(base):
    tg_id: PK
    is_authorized: bool
	generations: list(Generation), relationship(back_populates="user")
	feedbacks: list(GenerationFeedback),  relationship(back_populates="user")
class GenerationFeedback(Base): ...
  # основные поля теперь живут в Generation, а эта модель хранит только ссылку на генерацию. Можно было бы выбросить эту модель, но она нужна для RAG
------------------------------------------------------------------
