package com.rsiis.business.auth;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;

/**
 * users 表 Mapper (MyBatis-Plus BaseMapper, 常规 CRUD 由框架提供)。
 */
@Mapper
public interface UserMapper extends BaseMapper<UserEntity> {
}
