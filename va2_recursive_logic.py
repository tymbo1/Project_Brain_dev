import re
from collections import defaultdict

class RecursiveInferenceEngine:
    def __init__(self, rule_file='memory.sym'):
        self.rules = []
        self.facts = set()
        self.load_rules(rule_file)

    def load_rules(self, filename):
        with open(filename, 'r') as f:
            lines = f.readlines()
        for line in lines:
            if 'then' in line and 'if' in line:
                parts = re.split(r'if | then ', line.strip())
                if len(parts) == 3:
                    self.rules.append((parts[1].strip(), parts[2].strip()))
            elif line.strip():
                self.facts.add(line.strip())

    def infer(self):
        new_inference = True
        inferred = set()
        while new_inference:
            new_inference = False
            for condition, conclusion in self.rules:
                for fact in self.facts.copy():
                    if self.match_fact(condition, fact):
                        result = self.apply_conclusion(conclusion, fact)
                        if result and result not in self.facts:
                            self.facts.add(result)
                            inferred.add(result)
                            new_inference = True
        return inferred

    def match_fact(self, condition, fact):
        if 'X' in condition:
            condition_base = condition.replace('X', '')
            return condition_base in fact
        return condition in fact

    def apply_conclusion(self, conclusion, fact):
        if 'X' in conclusion:
            if 'X' in fact:
                x_value = fact.split()[0]
            else:
                x_value = fact.split()[0]
            return conclusion.replace('X', x_value)
        return conclusion

    def write_inference_log(self, filename='inference.log'):
        inferred = self.infer()
        if inferred:
            with open(filename, 'a') as f:
                for fact in sorted(inferred):
                    f.write(f'{fact}\n')
            return True
        return False
