#pragma once

#include <spawn.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>
#include <fcntl.h>
#include <poll.h>
#include <chrono>

#include "golpe.h"

// One persistent JSONL process, owned exclusively by the read-admission worker.
// No shared channel or lifecycle with the write policy, and no event payloads.
struct ReadPolicyProcess {
    using Clock = std::chrono::steady_clock;
    pid_t pid = -1;
    int inputFd = -1, outputFd = -1;

    ~ReadPolicyProcess() { stop(); }
    void stop() {
        if (inputFd >= 0) ::close(inputFd);
        if (outputFd >= 0) ::close(outputFd);
        inputFd = outputFd = -1;
        if (pid > 0) {
            ::kill(-pid, SIGKILL);
            while (::waitpid(pid, nullptr, 0) < 0 && errno == EINTR) {}
            pid = -1;
        }
    }
    void start(const std::string &command) {
        if (pid > 0) {
            siginfo_t status{};
            // Observe without reaping: stop() must kill the child group before
            // its leader's PID becomes reusable.
            if (::waitid(P_PID, pid, &status, WEXITED | WNOHANG | WNOWAIT) == 0 && !status.si_pid) return;
            stop();
            throw herr("read policy exited");
        }
        int input[2], output[2];
        if (::pipe2(input, O_CLOEXEC)) throw herr("read policy pipe failed");
        if (::pipe2(output, O_CLOEXEC)) {
            ::close(input[0]); ::close(input[1]);
            throw herr("read policy pipe failed");
        }
        posix_spawn_file_actions_t actions;
        posix_spawn_file_actions_init(&actions);
        posix_spawn_file_actions_adddup2(&actions, input[0], STDIN_FILENO);
        posix_spawn_file_actions_adddup2(&actions, output[1], STDOUT_FILENO);
        posix_spawnattr_t attrs;
        posix_spawnattr_init(&attrs);
        posix_spawnattr_setflags(&attrs, POSIX_SPAWN_SETPGROUP);
        posix_spawnattr_setpgroup(&attrs, 0);
        const char *argv[] = {"/bin/sh", "-c", command.c_str(), nullptr};
        extern char **environ;
        auto error = posix_spawnp(&pid, "sh", &actions, &attrs, const_cast<char **>(argv), environ);
        posix_spawn_file_actions_destroy(&actions);
        posix_spawnattr_destroy(&attrs);
        ::close(input[0]); ::close(output[1]);
        if (error) {
            ::close(input[1]); ::close(output[0]); pid = -1;
            throw herr("read policy spawn failed");
        }
        inputFd = input[1]; outputFd = output[0];
        if (::fcntl(inputFd, F_SETFL, O_NONBLOCK) || ::fcntl(outputFd, F_SETFL, O_NONBLOCK))
            throw herr("read policy nonblocking setup failed");
    }
    static void waitReady(int fd, short events, Clock::time_point deadline) {
        while (true) {
            auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
            if (ms <= 0) throw herr("read policy deadline");
            pollfd pfd{fd, events, 0};
            auto result = ::poll(&pfd, 1, ms);
            if (result < 0 && errno == EINTR) continue;
            if (result <= 0 || !(pfd.revents & events)) throw herr("read policy pipe unavailable");
            return;
        }
    }
    std::string decide(const std::string &command, uint64_t id, const std::vector<Bytes32> &keys, Clock::time_point deadline) {
        auto remaining = [&] {
            auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
            if (ms <= 0) throw herr("read policy deadline");
            return ms;
        };
        start(command);
        tao::json::value request = {{"type", "read-admission"}, {"request_id", std::to_string(id)},
                                   {"authenticated_pubkeys", tao::json::empty_array}};
        for (auto key : keys) request["authenticated_pubkeys"].push_back(to_hex(key.sv()));
        std::string wire = tao::json::to_string(request) + "\n";
        size_t written = 0;
        while (written < wire.size()) {
            waitReady(inputFd, POLLOUT, deadline);
            auto n = ::write(inputFd, wire.data() + written, wire.size() - written);
            if (n < 0 && (errno == EAGAIN || errno == EINTR)) continue;
            if (n <= 0) throw herr("read policy write failed");
            written += n;
        }
        std::string line;
        while (line.find('\n') == std::string::npos) {
            waitReady(outputFd, POLLIN, deadline);
            char buffer[4096];
            auto n = ::read(outputFd, buffer, sizeof(buffer));
            if (n < 0 && (errno == EAGAIN || errno == EINTR)) continue;
            if (n <= 0) throw herr("read policy read failed");
            line.append(buffer, n);
            if (line.size() > 4096) throw herr("oversized read policy response");
        }
        if (line.find('\n') != line.size() - 1) throw herr("unsolicited read policy response");
        auto response = tao::json::from_string(line);
        if (response.at("request_id").get_string() != std::to_string(id)) throw herr("read policy response mismatch");
        auto decision = response.at("decision").get_string();
        if (decision != "allow" && decision != "deny" && decision != "unavailable") throw herr("invalid read policy decision");
        remaining();
        return decision;
    }
};
