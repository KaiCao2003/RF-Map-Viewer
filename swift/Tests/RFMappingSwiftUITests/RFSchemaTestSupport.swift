import Foundation
@testable import RFMappingSwiftUI

func currentRFSchemaPayload(
    _ base: [String: Any],
    occupancyTimeSec: Any,
    occupancyTimeSecSize: [Int]
) -> [String: Any] {
    var payload = base
    payload["occupancyTimeSec"] = occupancyTimeSec
    payload["occupancyTimeSecSize"] = occupancyTimeSecSize
    payload["responseUnits"] = RFMappingData.expectedResponseUnits
    payload["responseNormalization"] = RFMappingData.expectedResponseNormalization
    payload["spikeCountDefinition"] = RFMappingData.expectedSpikeCountDefinition
    payload["occupancyTimeDefinition"] = RFMappingData.expectedOccupancyTimeDefinition
    return payload
}
