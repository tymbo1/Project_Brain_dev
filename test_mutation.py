from evolution_engine import EvolutionEngine

engine = EvolutionEngine()

# Clean mutations
engine.log("fire", "flame", 0.85, method="direct")
engine.log("flame", "ember", 0.92, method="bridge")
engine.log("ember", "ash", 0.77, method="decay")

# Cycle attempt: should be rejected
engine.log("ash", "fire", 0.20, method="loop_attempt")

# Summary
print("\nSummary:")
print(engine.summary())

# Export logs
engine.export("test_mutation_log.json")
engine.export_rejections("test_rejections_log.json")
