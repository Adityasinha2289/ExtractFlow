"""Generic Crawler component"""


class BaseCrawler:
    def __init__(self, config):
        self.config = config

    def crawl(self, start_url):
        pass
