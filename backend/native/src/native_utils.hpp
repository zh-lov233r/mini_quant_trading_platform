#pragma once

#include <string>
#include <string_view>

namespace quant_kernel {

std::string sha256_hex(std::string_view input);
std::string json_string(std::string_view value);

}  // namespace quant_kernel
