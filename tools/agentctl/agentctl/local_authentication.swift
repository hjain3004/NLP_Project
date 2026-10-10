import Foundation
import LocalAuthentication

let context = LAContext()
let semaphore = DispatchSemaphore(value: 0)
let arguments = Array(CommandLine.arguments.dropFirst())
guard arguments.count == 3 || arguments.count == 8 else {
    exit(3)
}
let action = arguments[0]
let targetHash = arguments[1]
let reason = arguments[2]
guard !action.isEmpty, targetHash.count == 64 else {
    exit(3)
}
var prompt = reason
if arguments.count == 8 {
    let targetType = arguments[2]
    let targetID = arguments[3]
    let scope = arguments[4]
    let boundReason = arguments[5]
    let approvalID = arguments[6]
    let nonce = arguments[7]
    guard !targetType.isEmpty, !targetID.isEmpty, !scope.isEmpty,
          !boundReason.isEmpty, !approvalID.isEmpty, nonce.count == 32 else {
        exit(3)
    }
    prompt = "Authorize exact approval\nAction: \(action)\nTarget type: \(targetType)\nTarget ID: \(targetID)\nTarget hash: \(targetHash)\nScope: \(scope)\nReason: \(boundReason)"
}
var authenticated = false
var cancelled = false

context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: prompt) { success, error in
    authenticated = success
    if let error = error as NSError?,
       error.domain == LAError.errorDomain,
       error.code == LAError.userCancel.rawValue {
        cancelled = true
    }
    semaphore.signal()
}

_ = semaphore.wait(timeout: .distantFuture)
if authenticated {
    print("authenticated")
    exit(0)
}
if cancelled {
    exit(2)
}
exit(3)
