class AppException(Exception):
    """应用基础异常。"""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundException(AppException):
    """资源不存在。"""

    def __init__(self, message: str = "请求的资源不存在"):
        super().__init__(message, status_code=404)


class ValidationException(AppException):
    """数据验证失败。"""

    def __init__(self, message: str = "数据验证失败"):
        super().__init__(message, status_code=422)


class ConnectorException(AppException):
    """数据源连接/读取异常。"""

    def __init__(self, message: str = "数据源操作失败"):
        super().__init__(message, status_code=500)


class LLMException(AppException):
    """LLM 调用异常。"""

    def __init__(self, message: str = "大模型调用失败"):
        super().__init__(message, status_code=500)


class PipelineException(AppException):
    """清洗流水线异常。"""

    def __init__(self, message: str = "清洗流程执行失败"):
        super().__init__(message, status_code=500)
