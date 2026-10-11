"use client";

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";

import { FOOTER_HEIGHT, HEADER_HEIGHT, ROW_HEIGHT, type PlacedNode } from "@/lib/graph/layout";

export type TableNodeData = {
  placed: PlacedNode;
  expanded: boolean;
  onToggle: (table: string) => void;
};

export type TableNodeType = Node<TableNodeData, "table">;

// A handle says where a line meets its row. It is not something to see.
const HIDDEN = { opacity: 0 };

/**
 * A column's label inside its table: its readable name without the
 * table's, and "key" for "surrogate key". "store sales — sold date
 * surrogate key" -> "sold date key".
 */
export function columnLabel(readable: string): string {
  return readable
    .split(" — ")
    .slice(-1)[0]
    .replace(/\bsurrogate key\b/, "key");
}

/**
 * A table, with its columns listed inside it (DD-18). Every listed column
 * carries a handle on each side, so a key leaves from its own row and two
 * keys of one table are two visibly different lines.
 */
export function TableNode({ data }: NodeProps<TableNodeType>) {
  const { placed, expanded, onToggle } = data;
  const { node, columns, hidden } = placed;
  const total = node.columns.length;

  return (
    <div
      className={`rounded-md border bg-white text-gray-900 shadow-sm ${
        node.inTree ? "border-gray-400" : "border-dashed border-gray-300"
      }`}
      style={{ width: placed.width, height: placed.height }}
    >
      <div
        className={`flex flex-col justify-center rounded-t-md border-b px-3 ${
          node.anchor ? "bg-slate-100" : "bg-white"
        }`}
        style={{ height: HEADER_HEIGHT }}
      >
        <span className="truncate text-[15px] font-semibold leading-tight" data-graph="title">
          {node.readable}
        </span>
        <span className="truncate font-mono text-[12px] text-gray-500">{node.id}</span>
      </div>

      {columns.map((column) => (
        <div
          key={column.name}
          className="relative flex items-center gap-2 px-3 text-[12.5px]"
          style={{ height: ROW_HEIGHT }}
          title={`${column.readable} (${column.dataType})`}
        >
          <Handle type="target" position={Position.Left} id={`in:${column.name}`} isConnectable={false} style={HIDDEN} />
          <span className="shrink-0 font-mono" data-graph="column">
            {column.name}
          </span>
          <span className="ml-auto min-w-0 truncate text-[12px] text-gray-500" data-graph="label">
            {columnLabel(column.readable)}
          </span>
          {(column.primaryKey || column.foreignKey) && (
            <span className="shrink-0 rounded bg-gray-100 px-1 text-[10px] font-medium uppercase text-gray-600">
              {column.primaryKey ? "pk" : "fk"}
            </span>
          )}
          <Handle type="source" position={Position.Right} id={`out:${column.name}`} isConnectable={false} style={HIDDEN} />
        </div>
      ))}

      <button
        type="button"
        className="nodrag flex w-full items-center justify-between rounded-b-md border-t px-3 text-[12px] text-gray-600 hover:bg-gray-50"
        data-graph="footer"
        style={{ height: FOOTER_HEIGHT }}
        onClick={() => onToggle(node.id)}
        disabled={!expanded && hidden === 0}
      >
        <span>
          showing {columns.length} of {total} columns
        </span>
        {(expanded || hidden > 0) && (
          <span className="font-medium text-blue-700">{expanded ? "show fewer" : `show all ${total}`}</span>
        )}
      </button>
    </div>
  );
}
