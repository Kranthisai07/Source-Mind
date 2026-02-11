# Research-Grade System Architecture: SourceMind
## Collaborative Memory & Attribution Intelligence Platform

**Target Venues:** AAAI, UIST, CSCW, NeurIPS (Applied/Systems Track)  
**Authors:** [Your Name]  
**Date:** February 11, 2026

---

## 1. Abstract & Problem Formulation

### 1.1 Problem Statement: The Attribution-Knowledge Gap
Modern collaborative environments are characterized by **Heterogeneous Agent Interaction (HAI)**, where knowledge is co-created by human experts and internal/external tools. Existing knowledge management systems (KMS) treat content as static artifacts, failing to capture the **provenance vectors**—the specific user or tool that contributed to the final state. This leads to the **Attribution-Knowledge Gap**, where the *semantic meaning* of data is preserved, but the *causal history* and *ownership* are lost.

### 1.2 Proposed Solution: SourceMind Architecture
We propose **SourceMind**, a dual-layer knowledge architecture that explicitly couples a **Semantic Vector Space** with a **Temporal Attribution Graph**. Unlike traditional RAG (Retrieval-Augmented Generation) systems that optimize solely for relevance, SourceMind optimizes for **Attributed Relevance**, ranking knowledge retrieval based on both semantic similarity and contributor authority.

---

## 2. High-Level System Design

The architecture follows a modular **Lambda Architecture** variant, capable of handling batch processing (embedding generation, graph analytics) and real-time interactions (memory creation, search).

### 2.1 Core Architectural Layers

1.  **Ingestion & Normalization Layer**: Abstracts heterogeneous input sources (IDE, Chat, Docs) into a unified `MemoryFragment` schema.
2.  **The Attribution Engine (AE)**: A subsystem that tracks authorship and edit history across multiple sessions and users.
3.  **Semantic-Relational Storage Layer**: A hybrid storage substrate combining dense vector embeddings (pgvector) with relational graph topology (PostgreSQL).
4.  **Context-Aware Retrieval Module (CARM)**: A re-ranking pipeline that fuses vector similarity with graph centrality and access control logic.
5.  **Interface & Protocol Layer**: Exposes system capabilities via standard RESTful APIs and the **Model Context Protocol (MCP)** for agentic integration.

---

## 3. Comprehensive Data Flow

```mermaid
graph TD
    %% Nodes
    subgraph Data_Sources [Heterogeneous Data Sources]
        User[Human Expert]
        AI[Internal Tools / Agents]
        Ext[External Integrations]
    end

    subgraph Ingestion_Layer [L1: Ingestion & Normalization]
        API[API Gateway (NestJS)]
        Norm[Normalization Service]
        Queue[Event Queue (Redis/BullMQ)*]
    end

    subgraph Processing_Layer [L2: Intelligence Processing]
        Emb[Embedding Service (OpenAI text-embedding-3)]
        
        subgraph Attribution_Engine [Attribution Engine]
            Diff[Diff/Edit History Tracker]
            Score[Human Contrib. Scoring]
            Log[Activity Logger / Tool Provenance]
        end
        
        NER[Entity Extraction (NER)*]
    end

    subgraph Storage_Layer [L3: Semantic-Relational Storage]
        PG[(PostgreSQL Primary)]
        Vec[(Vector Index HNSW)]
        Graph[(Relational Graph Nodes)]
        
        PG <--> Vec
        PG <--> Graph
    end

    subgraph Retrieval_Layer [L4: Context-Aware Retrieval]
        Hybrid[Hybrid Search Strategy]
        Rank[Re-Ranking Algorithm]
        ACL[RBAC Enforcement Filter]
    end

    subgraph Application_Layer [L5: Application & Analytics]
        Web[Web Client (Next.js)]
        Agents[Agentic Clients (MCP)]
        Dash[Analytics Dashboard]
    end

    %% Edges
    User --> API
    AI --> API
    Ext --> API

    API --> Norm
    Norm --> Queue
    Queue --> Emb
    Queue --> Attribution_Engine
    Queue --> NER

    Emb --> Vec
    Attribution_Engine --> Graph
    NER --> PG

    Web --> Hybrid
    Agents --> Hybrid
    
    Hybrid --> ACL
    ACL --> PG
    ACL --> Vec
    
    Rank --> Web
```
*(Items marked with * represent planned or implicit architectural components for scalability)*

---

## 4. Detailed Component Analysis

### 4.1 Ingestion & Normalization Layer
**Objective:** Reduce entropy from diverse input streams.
*   **Mechanism**: Incoming data payloads (code snippets, decision logs, chat messages) are standardized into a `Memory` entity containing raw content, `MemorySource` metadata (User/Tool), and timestamp vectors.
*   **Research Validity**: Ensures the downstream engine operates on a consistent data manifold, crucial for reliable attribution tracking.

### 4.2 The Attribution Engine (AE)
**Objective:** Track the "ownership" and evolution of a knowledge artifact.
*   **Mechanism**: **Temporal Edit Tracking & Human Scoring**.
    When content is modified, the system records the differential change ($\Delta$). For human users, a normalized **Contribution Score** is computed based on edit magnitude. AI/Tool edits are tracked for provenance but excluded from the percentage calculation.
*   **Future Research Component**: Semantic Change Detection. Instead of character-level diffs, we calculate the shift in the embedding space $\Delta \vec{E}$ caused by an edit to understand the *significance* of a contribution.
*   **Output**: An `Attribution` record stored alongside the memory, containing a contribution score for humans and an activity log for tools.

### 4.3 Semantic-Relational Storage (SRS)
**Objective:** Persist knowledge with both semantic and topological context.
*   **Vector Substrate**: We utilize **HNSW (Hierarchical Navigable Small World)** indexing via `pgvector` for $O(\log N)$ approximate nearest neighbor search. Dimensionality: 1536 (OpenAI `text-embedding-3-large`).
*   **Graph Overlay**: Memories are nodes in a graph. Edges represent typed relationships (`supersedes`, `relates_to`, `updates`).
*   **Justification**: Pure vector stores lack structural logic (A supersedes B). Pure graph DBs lack semantic fuzziness. The SRS hybrid approach captures both.

### 4.4 Context-Aware Retrieval Module (CARM)
**Objective:** Retrieve the "most useful" information, not just the "most similar."
*   **Algorithm**:
    1.  **Candidate Generation**: Retrieve Top-K via Cosine Similarity (`vector_cosine_ops`).
    2.  **Graph Expansion**: Fetch immediate neighbors (1-hop) in the Semantic Graph (e.g., "Get the decision that superseded this result").
    3.  **Attribution Filtering**: Optional filtering by specific authors (e.g., "Only show updates from Senior Engineers").
    4.  **Security Filtering**: Apply Hierarchical RBAC (Workspace → Project → Memory) at the query level to ensure data governance.

---

## 5. Deployment & Infrastructure

*   **Compute**:
    *   **Stateless Services**: NestJS microservices (containerized via Docker) handling API logic.
    *   **Stateful Services**: Managed PostgreSQL (Supabase) for ACID compliance on attribution transactions.
*   **Observation & Ops**:
    *   **Embedding Monitor**: Tracks distribution drift in memory embeddings to detect topic shifts.
    *   **Attribution Audit Log**: Immutable ledger of all contribution events for reproducibility.

---

## 6. Security, Scalability & Reliability

### 6.1 Scalability Considerations
*   **Horizontal Partitioning**: Architecture supports sharding `Memories` by `WorkspaceID`. Since search is scoped to workspaces, this allows linear scaling of storage and compute.
*   **Read-Replicas**: Vector similarity inputs are read-heavy; standard Postgres replication handles high query throughput.
*   **Asynchronous Embedding**: The `Queue` component (Ref 4.1) allows the system to absorb high-velocity write bursts without blocking the API response, critical for real-time collaborative editing sessions.

### 6.2 Security Design
*   **Zero-Trust Attribution**: The AE assumes no inherent trust in the input source metadata; it re-validates authorship claims against authentication tokens.
*   **Granular RBAC**: Implementing "Row Level Security" patterns logic within the application layer minimizes the blast radius of potential data leaks.

---

## 7. Extensions & Future Work

*   **Federated Knowledge Graphs**: Extending the architecture to query across disparate SourceMind instances without centralizing data (Privacy-Preserving Collaboration).
*   **Generative Explanation**: Utilizing LLMs to generate natural language explanations for *why* a specific set of memories was retrieved, citing the specific attribution paths (e.g., "Retrieved because Expert A modified this section heavily").

---

**Note to Reviewers:** This architecture represents a novel integration of systems engineering and AI interaction design, creating a verifiable methodology for tracking intellectual provenance in increasing automated workflows.
