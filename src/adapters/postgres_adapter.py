from contextlib import contextmanager
from sqlalchemy import create_engine
import os
# from adapters.azure_adapter import get_secret
# from modules.configs.env_config import azure_config
# from modules.configs.log_config import logger

from utils.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

POSTGRES_CONNECTION_STRING_SECRET_KEY = os.getenv("POSTGRES_CONNECTION_STRING_SECRET_KEY")

# sqlalchemy_engine = create_engine(
#     get_secret(azure_config.POSTGRES_CONNECTION_STRING_SECRET_KEY)
# )

sqlalchemy_engine = create_engine(
    POSTGRES_CONNECTION_STRING_SECRET_KEY
)

@contextmanager
def sqlalchemy_session(autocommit=True) -> create_engine:
    """
    Provide a transactional scope around a series of operations.

    :param autocommit: If True, each statement is automatically committed.
    """
    logger.info("Creating connection to database")
    connection = sqlalchemy_engine.connect()

    if autocommit:
        # Turn autocommit on
        connection.execution_options(isolation_level="AUTOCOMMIT")
        logger.info("Autocommit mode enabled")

    try:
        yield connection
    except Exception as e:
        if not autocommit:
            logger.error(f"An error occurred in the database, ROLLING BACK: {str(e)}")
            connection.execute("ROLLBACK")
        raise
    else:
        if not autocommit:
            logger.info("Committing transaction")
            connection.execute("COMMIT")
    finally:
        logger.info("Closing connection to database")
        connection.close()
