"use client";

import { createContext } from "react";

/** A DataTableCard supplies its heading id to descendant table primitives so
 * every table has a programmatic name without duplicating visible captions. */
export const DataTableLabelContext = createContext<string | undefined>(undefined);
