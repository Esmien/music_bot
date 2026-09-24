class EnrichedSongPrompt(BaseModel): 
	genre_and_style: str | None = None 
	mood: str | None = None 
	instrumentation: list[str] = Field(default_factory=list) 
	tempo_bpm: int | None = None 
	# и так далее...