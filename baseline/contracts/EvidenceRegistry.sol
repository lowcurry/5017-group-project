// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title EvidenceRegistry
/// @notice Minimal append-only registry for auditable research-package hashes.
contract EvidenceRegistry {
    struct Record {
        address submitter;
        uint64 blockNumber;
        uint64 timestamp;
        string uri;
    }

    mapping(bytes32 => Record) private records;

    event EvidenceRegistered(
        bytes32 indexed packageHash,
        address indexed submitter,
        string uri
    );

    function register(bytes32 packageHash, string calldata uri) external {
        require(packageHash != bytes32(0), "empty hash");
        require(records[packageHash].timestamp == 0, "already registered");
        records[packageHash] = Record({
            submitter: msg.sender,
            blockNumber: uint64(block.number),
            timestamp: uint64(block.timestamp),
            uri: uri
        });
        emit EvidenceRegistered(packageHash, msg.sender, uri);
    }

    function lookup(bytes32 packageHash) external view returns (Record memory) {
        return records[packageHash];
    }

    function exists(bytes32 packageHash) external view returns (bool) {
        return records[packageHash].timestamp != 0;
    }
}
