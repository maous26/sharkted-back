class BaseCollector:
    source: str

    def fetch(self, url: str):
        raise NotImplementedError

    def parse(self, raw):
        raise NotImplementedError

    def score(self, deal):
        return 0

    def run(self, url: str):
        raw = self.fetch(url)
        deals = self.parse(raw)
        return [
            {**deal, "score": self.score(deal)}
            for deal in deals
        ]
