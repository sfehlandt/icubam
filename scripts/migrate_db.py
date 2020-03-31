from absl import app
from absl import flags
from icubam import config
from icubam.db import sqlite
from icubam.db import store
from icubam.db import migrator

flags.DEFINE_string("config", "resources/config.toml", "Config file.")
flags.DEFINE_string("dotenv_path", "resources/.env", "Config file.")
flags.DEFINE_string("old_db_path", "something.db", "sqlite file.")
flags.DEFINE_enum("mode", "dev", ["prod", "dev"], "Run mode.")
FLAGS = flags.FLAGS


def main(unused_argv):
  cfg = config.Config(FLAGS.config, mode=FLAGS.mode, env_path=FLAGS.dotenv_path)
  mgt = migrator.Migrator(cfg, FLAGS.old_db_path)
  mgt.run()


if __name__ == "__main__":
  app.run(main)
