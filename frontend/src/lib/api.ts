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
