-- postgres 镜像只会按 POSTGRES_DB 建「一个」库。
-- 本项目有两个：业务库由 POSTGRES_DB 建，会话检查点库在这里补。
-- 检查点库单独放，是因为它是每轮都写的高频小事务，和业务慢查询混在一起会互相拖累。
CREATE DATABASE agent_checkpoints;
