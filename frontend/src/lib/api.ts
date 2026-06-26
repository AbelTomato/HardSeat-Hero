const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface RouteSearchRequest {
  from_station: string;
  to_station: string;
  date: string;
  max_transfers: number;
  min_transfer_minutes: number;
}

export interface TrainSegment {
  train_no: string;
  from_station: string;
  to_station: string;
  depart_at: string;
  arrive_at: string;
  duration_minutes: number;
  prices: Array<{ seat_type: string; price: string; remaining: string }>;
  source: string;
  updated_at: string;
}

export interface TransferPlan {
  total_price: string;
  total_duration_minutes: number;
  transfer_minutes: number;
  transfer_stations: string[];
  segments: TrainSegment[];
}

export interface RouteSearchResponse {
  query_id: string;
  source: string;
  updated_at: string;
  plans: TransferPlan[];
}

export interface StationSearchResponse {
  stations: string[];
}

export interface ProviderStatusResponse {
  provider: string;
  status: string;
  transfer_candidate_enabled: boolean;
  max_remote_queries: number;
  max_concurrent_remote_queries: number;
  last_remote_query_count: number;
  last_diagnostics: SearchDiagnostics;
  updated_at: string;
}

export interface SearchDiagnostics {
  remote_query_count: number;
  memory_cache_hit_count: number;
  persistent_cache_hit_count: number;
  expanded_candidates: string[];
  failed_candidates: string[];
  pruned_by_best_price_count: number;
  pruned_by_pareto_count: number;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number | null,
    public readonly category:
      | "backend_unavailable"
      | "data_source_unavailable"
      | "bad_request"
      | "unknown",
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function searchStations(query: string): Promise<string[]> {
  const params = new URLSearchParams({ q: query });
  const response = await fetch(
    `${API_BASE_URL}/api/stations/search?${params.toString()}`,
  );

  if (!response.ok) {
    throw await buildApiError(response, "站点查询失败");
  }

  const body: StationSearchResponse = await response.json();
  return body.stations;
}

export async function searchRoutes(
  payload: RouteSearchRequest,
): Promise<RouteSearchResponse> {
  const response = await fetch(`${API_BASE_URL}/api/routes/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw await buildApiError(response, "查询失败");
  }

  return response.json();
}

export async function searchRoutesStream(
  payload: RouteSearchRequest,
  onPlan: (plan: TransferPlan, response: RouteSearchResponse) => void,
  signal?: AbortSignal,
): Promise<RouteSearchResponse> {
  const response = await fetch(`${API_BASE_URL}/api/routes/search/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok) {
    throw await buildApiError(response, "查询失败");
  }
  if (!response.body) {
    throw new ApiError("浏览器不支持流式读取响应。", null, "unknown");
  }

  const result: RouteSearchResponse = {
    query_id: `${payload.date}:${payload.from_station}:${payload.to_station}`,
    source: "stream",
    updated_at: new Date().toISOString(),
    plans: [],
  };
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += value;
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      consumePlanLine(line, result, onPlan);
    }
  }
  if (buffer.trim()) {
    consumePlanLine(buffer, result, onPlan);
  }

  return result;
}

export async function getProviderStatus(): Promise<ProviderStatusResponse> {
  const response = await fetch(`${API_BASE_URL}/api/providers/status`);

  if (!response.ok) {
    throw await buildApiError(response, "数据源状态查询失败");
  }

  return response.json();
}

async function buildApiError(
  response: Response,
  fallbackMessage: string,
): Promise<ApiError> {
  const detail = await readErrorDetail(response);
  if (response.status === 422) {
    return new ApiError(
      detail ?? "请求参数不符合要求，请检查站点、日期和换乘时间。",
      response.status,
      "bad_request",
    );
  }
  if (
    response.status === 502 ||
    response.status === 503 ||
    response.status === 504
  ) {
    return new ApiError(
      detail ?? "数据源暂不可用，请稍后重试或切回 Mock 数据源。",
      response.status,
      "data_source_unavailable",
    );
  }
  return new ApiError(
    detail ?? `${fallbackMessage}：${response.status}`,
    response.status,
    "unknown",
  );
}

function consumePlanLine(
  line: string,
  result: RouteSearchResponse,
  onPlan: (plan: TransferPlan, response: RouteSearchResponse) => void,
) {
  if (!line.trim()) {
    return;
  }
  const parsed = JSON.parse(line) as TransferPlan | { error?: string };
  if ("error" in parsed && parsed.error) {
    throw new ApiError(parsed.error, 502, "data_source_unavailable");
  }
  const plan = parsed as TransferPlan;
  result.plans = [...result.plans, plan]
    .sort((left, right) => comparePlans(left, right))
    .slice(0, 20);
  onPlan(plan, { ...result, plans: result.plans });
}

function comparePlans(left: TransferPlan, right: TransferPlan) {
  const priceDiff = Number(left.total_price) - Number(right.total_price);
  if (priceDiff !== 0) {
    return priceDiff;
  }
  const durationDiff =
    left.total_duration_minutes - right.total_duration_minutes;
  if (durationDiff !== 0) {
    return durationDiff;
  }
  return left.transfer_stations.length - right.transfer_stations.length;
}

async function readErrorDetail(response: Response): Promise<string | null> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    return null;
  }
  return null;
}
