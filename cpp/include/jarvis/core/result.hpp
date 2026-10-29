#pragma once

#include <string>
#include <utility>
#include <variant>

namespace jarvis {

// std::expected C++23'te geldiği için C++20 uyumlu hafif bir eşdeğeri.
// Ya başarı değeri (T) ya da hata (E) taşır; ham işaretçi/istisna kullanmaz.
template <typename T, typename E>
class Result {
public:
    Result(T value) : state_(std::move(value)) {}
    Result(E error) : state_(std::move(error)) {}

    static Result ok(T value) { return Result(std::move(value)); }
    static Result err(E error) { return Result(std::move(error)); }

    bool has_value() const noexcept { return std::holds_alternative<T>(state_); }
    explicit operator bool() const noexcept { return has_value(); }

    T& value() & { return std::get<T>(state_); }
    const T& value() const& { return std::get<T>(state_); }
    T&& value() && { return std::get<T>(std::move(state_)); }

    E& error() & { return std::get<E>(state_); }
    const E& error() const& { return std::get<E>(state_); }

    T value_or(T fallback) const {
        return has_value() ? std::get<T>(state_) : std::move(fallback);
    }

private:
    std::variant<T, E> state_;
};

}  // namespace jarvis
