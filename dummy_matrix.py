class DummyIdentityMatrix:
    def evaluate(self, term):
        if "fire" in term:
            return 0.8
        elif "myth" in term:
            return 0.2
        return 0.4
